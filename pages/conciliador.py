import streamlit as st
import pandas as pd
import pdfplumber
import mysql.connector
import io
import re
import unicodedata
import uuid
from thefuzz import fuzz
from ofxparse import OfxParser
import logging
import time

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

# =============================================================================
# CLASSE UNDO STACK (MOTOR DE DESFAZER)
# =============================================================================
class UndoStack:
    def __init__(self):
        if 'undo_stack' not in st.session_state:
            st.session_state.undo_stack = []

    def push(self, action_type, data):
        st.session_state.undo_stack.append({'type': action_type, 'data': data})

    def pop(self):
        if st.session_state.undo_stack:
            return st.session_state.undo_stack.pop()
        return None

    def clear(self):
        st.session_state.undo_stack = []

    def is_empty(self):
        return not bool(st.session_state.undo_stack)

undo_manager = UndoStack()

# =============================================================================
# 1. UTILITÁRIOS, CONEXÃO E FILTROS GLOBAIS
# =============================================================================
def get_connection():
    try:
        return mysql.connector.connect(
            host=st.secrets["mysql"]["host"],
            user=st.secrets["mysql"]["user"],
            password=st.secrets["mysql"]["password"],
            database=st.secrets["mysql"]["database"],
            use_pure=True,
            ssl_disabled=True
        )
    except mysql.connector.Error as err:
        logging.error(f"Erro ao conectar ao MySQL: {err}")
        st.error(f"Erro ao conectar ao banco de dados: {err}")
        st.stop()

def padronizar_texto(texto):
    if not texto:
        return ""
    texto_sem_acento = unicodedata.normalize('NFKD', str(texto)).encode('ASCII', 'ignore').decode('utf-8')
    return re.sub(r'\s+', ' ', texto_sem_acento.upper().strip())

def formatar_moeda(valor):
    return f"R$ {valor:,.2f}".replace(',', 'X').replace('.', ',').replace('X', '.')

def limpar_cnpj(cnpj_str):
    if not cnpj_str:
        return ""
    return re.sub(r'[^0-9]', '', str(cnpj_str))

def formatar_cnpj(cnpj_limpo):
    if not cnpj_limpo or len(cnpj_limpo) != 14:
        return cnpj_limpo
    return f"{cnpj_limpo[:2]}.{cnpj_limpo[2:5]}.{cnpj_limpo[5:8]}/{cnpj_limpo[8:12]}-{cnpj_limpo[12:]}"

def eh_linha_de_saldo(descricao):
    d = padronizar_texto(descricao)
    if 'SALDO' in d or 'SDO' in d:
        bloqueios = [
            'SALDO ANTERIOR', 'SALDO FINAL', 'SALDO DO DIA', 'SALDO DIA', 
            'SALDO EM', 'SDO FINAL', 'SDO ANTERIOR', 'SDO CT', 'SALDO BLOQUEADO'
        ]
        if any(b in d for b in bloqueios):
            return True
        if d == 'SALDO' or d == 'SDO':
            return True
        if d.startswith('SALDO ') or d.startswith('SDO '):
            return True
    return False

def limpar_cod_historico(cod):
    """Garante que códigos como 207.0 ou 203.0 sejam lidos apenas como 207 ou 203"""
    if not cod or pd.isna(cod) or str(cod).strip() == "" or str(cod).strip().upper() == "NAN":
        return ""
    try:
        return str(int(float(cod)))
    except ValueError:
        return str(cod).strip()

# =============================================================================
# 2. MOTOR DE RECÁLCULO EM TEMPO REAL
# =============================================================================
def aplicar_regras_aos_extratos(df_bruto, id_empresa, banco_selecionado, conta_banco_fixa):
    if df_bruto.empty:
        return

    conn = get_connection()
    regras = pd.read_sql(
        """SELECT * FROM tb_extratos_regras
           WHERE id_empresa = %s AND banco_nome = %s
           ORDER BY LENGTH(termo_chave) DESC""",
        conn,
        params=(id_empresa, banco_selecionado)
    )
    conn.close()

    prontos, pendentes, linhas_ignoradas_regras = [], [], []

    # Se a lista de ignorados não existe, cria
    if 'linhas_ignoradas_regras' not in st.session_state:
        st.session_state.linhas_ignoradas_regras = []

    for idx, row in df_bruto.iterrows():
        # Pula se o usuário excluiu manualmente este index
        if idx in st.session_state.linhas_ignoradas_regras:
            continue

        match = False
        for _, r in regras.iterrows():
            termo_padrao = padronizar_texto(r['termo_chave'])
            palavras_chave = termo_padrao.split()
            
            contem_todas = all(palavra in row['Descricao'] for palavra in palavras_chave)
            
            if (contem_todas or fuzz.ratio(termo_padrao, row['Descricao']) >= 85) and r['sinal_esperado'] == row['Sinal']:
                if r['conta_contabil'] == 'IGNORAR':
                    linhas_ignoradas_regras.append(idx)
                else:
                    debito_conta  = conta_banco_fixa if row['Sinal'] == '+' else r['conta_contabil']
                    credito_conta = r['conta_contabil'] if row['Sinal'] == '+' else conta_banco_fixa
                    prontos.append({
                        'idx_original':  str(idx), # Guardamos o ID para poder excluir depois
                        'Debito':        debito_conta,
                        'Credito':       credito_conta,
                        'Data':          row['Data'],
                        'Valor':         f"{row['Valor']:.2f}".replace('.', ','),
                        'Cod_Historico': limpar_cod_historico(r['cod_historico_erp']),
                        'Historico':     r['historico_padrao'] if r['historico_padrao'] else row['Descricao']
                    })
                match = True
                break
        if not match:
            pendentes.append({'idx_original': idx, **row})

    # Adiciona os lançamentos manuais ao bolo final
    if 'lancamentos_manuais' in st.session_state and st.session_state.lancamentos_manuais:
        prontos.extend(st.session_state.lancamentos_manuais)

    st.session_state.prontos                 = prontos
    st.session_state.pendentes               = pd.DataFrame(pendentes)
    
    # Mantém as exclusões manuais seguras unindo com as novas ignoradas
    todas_ignoradas = list(set(st.session_state.linhas_ignoradas_regras + linhas_ignoradas_regras))
    st.session_state.linhas_ignoradas_regras = todas_ignoradas

# =============================================================================
# DEFESAS CONTRA EXCEL BANCÁRIO (O Conversor e o Leitor Robusto)
# =============================================================================
def converter_data_excel(data_raw):
    data_raw = str(data_raw).strip()
    match = re.search(r'^(\d{2})/(\d{2})/(\d{2,4})', data_raw)
    if match:
        ano = match.group(3)
        if len(ano) == 2: ano = '20' + ano
        return f"{match.group(1)}/{match.group(2)}/{ano}"
    
    match = re.search(r'^(\d{4})-(\d{2})-(\d{2})', data_raw)
    if match:
        return f"{match.group(3)}/{match.group(2)}/{match.group(1)}"
        
    return None

def ler_planilha_robusto(file_bytes, nome_arquivo):
    nome_min = nome_arquivo.lower()
    if nome_min.endswith('.csv'):
        try: return pd.read_csv(io.BytesIO(file_bytes), sep=';', header=None, dtype=str, encoding='utf-8')
        except: return pd.read_csv(io.BytesIO(file_bytes), sep=';', header=None, dtype=str, encoding='latin1')
    else:
        try: 
            return pd.read_excel(io.BytesIO(file_bytes), header=None, dtype=str)
        except Exception:
            try:
                dfs = pd.read_html(io.BytesIO(file_bytes), encoding='utf-8', decimal=',', thousands='.')
                return dfs[0].astype(str) if dfs else pd.DataFrame()
            except:
                try:
                    dfs = pd.read_html(io.BytesIO(file_bytes), encoding='latin1', decimal=',', thousands='.')
                    return dfs[0].astype(str) if dfs else pd.DataFrame()
                except:
                    return pd.DataFrame()

# =============================================================================
# 3. INTELIGÊNCIA: AUTO-LEITURA E EXTRAÇÃO
# =============================================================================
BBOX_HEADER_AREA    = (0, 0, 600, 150)
BBOX_BANK_NAME_AREA = (50, 0, 550, 150)

BANCOS_KEYWORDS = {
    'STONE':     ['STONE', 'INSTITUIÇÃO DE PAGAMENTO'],
    'SICOOB':    ['SICOOB', 'BANCOOB', 'SICOOB BANCO', 'CREDIMATA'],
    'BRADESCO':  ['BRADESCO', 'BANCO BRADESCO'],
    'ITAU':      ['ITAU', 'BANCO ITAU'],
    'CAIXA':     ['CAIXA', 'CAIXA ECONOMICA FEDERAL'],
    'SANTANDER': ['SANTANDER', 'BANCO SANTANDER'],
    'BB':        ['BANCO DO BRASIL', 'BB'],
    'NUBANK':    ['NUBANK', 'NU PAGAMENTOS'],
}

def identificar_cnpj_no_pdf(file_bytes):
    try:
        with pdfplumber.open(io.BytesIO(file_bytes)) as pdf:
            if len(pdf.pages) > 0:
                header_text = pdf.pages[0].crop(BBOX_HEADER_AREA).extract_text()
                if not header_text: return None
                cnpj_match = re.search(r'\d{2}\.?\d{3}\.?\d{3}/?\d{4}-?\d{2}', header_text)
                if cnpj_match: return cnpj_match.group(0)
    except Exception: pass
    return None

def identificar_banco_no_pdf(file_bytes):
    try:
        with pdfplumber.open(io.BytesIO(file_bytes)) as pdf:
            if len(pdf.pages) > 0:
                header_text = pdf.pages[0].crop(BBOX_BANK_NAME_AREA).extract_text()
                if not header_text: return "DESCONHECIDO"
                header_upper = padronizar_texto(header_text)
                for banco, keywords in BANCOS_KEYWORDS.items():
                    for kw in keywords:
                        if padronizar_texto(kw) in header_upper: return banco
    except Exception: pass
    return "DESCONHECIDO"

@st.cache_data(show_spinner=False)
def buscar_empresa_por_cnpj_otimizado(cnpj_formatado, df_empresas):
    if not cnpj_formatado: return None
    cnpj_limpo_buscado = limpar_cnpj(cnpj_formatado)
    if 'cnpj_limpo' not in df_empresas.columns:
        df_empresas = df_empresas.copy()
        df_empresas['cnpj_limpo'] = df_empresas['cnpj'].astype(str).apply(limpar_cnpj)
    match_df = df_empresas[df_empresas['cnpj_limpo'] == cnpj_limpo_buscado]
    if not match_df.empty: return match_df.iloc[0].to_dict()
    return None

@st.cache_data(ttl=60, show_spinner=False)
def buscar_conta_por_banco(id_empresa, nome_banco):
    conn = None
    try:
        conn = get_connection()
        cursor = conn.cursor(dictionary=True)
        cursor.execute("SELECT conta_contabil FROM empresa_banco_contas WHERE id_empresa = %s AND nome_banco = %s", (id_empresa, nome_banco))
        result = cursor.fetchone()
        return result['conta_contabil'] if result else None
    except mysql.connector.Error: return None
    finally:
        if conn: conn.close()

# ==========================================
# MOTOR GENÉRICO PDF (STONE, ETC)
# ==========================================
@st.cache_data(show_spinner=False)
def extrair_por_recintos(file_bytes):
    texto_completo = ""
    with pdfplumber.open(io.BytesIO(file_bytes)) as pdf:
        for page in pdf.pages:
            palavras = page.extract_words(x_tolerance=3, y_tolerance=3)
            linhas_dict = {}
            for p in palavras:
                y = round(float(p['top']))
                encontrou_y = next((k for k in linhas_dict if abs(k - y) <= 3), None)
                if encontrou_y is not None: linhas_dict[encontrou_y].append(p)
                else: linhas_dict[y] = [p]
            for y_key in sorted(linhas_dict.keys()):
                linha_ordenada = sorted(linhas_dict[y_key], key=lambda x: x['x0'])
                texto_completo += " ".join([w['text'] for w in linha_ordenada]) + "\n"

    RUIDO_CABECALHO = ["período:", "página", "cnpj", "emitido em", "extrato de conta", "dados da conta", "nome documento", "instituição agência", "contraparte stone"]
    linhas = [l.strip() for l in texto_completo.split('\n') if l.strip()]
    dados, ignoradas_raw = [], []
    regex_data  = r'\d{2}/\d{2}/\d{2,4}'
    regex_valor = r'-?\s*(?:R\$?\s*)?\d{1,3}(?:\.\d{3})*,\d{2}'

    for linha in linhas:
        if any(x in linha.lower() for x in RUIDO_CABECALHO): continue
        match_data = re.search(regex_data, linha)
        valores    = re.findall(regex_valor, linha)

        if match_data and valores:
            data        = match_data.group(0)
            valor_bruto = valores[0]
            is_negativo = '-' in valor_bruto or bool(re.search(r'\sD$', linha.strip(), re.IGNORECASE))
            is_positivo = '+' in valor_bruto or bool(re.search(r'\sC$', linha.strip(), re.IGNORECASE))

            valor_str_limpo = re.search(r'\d{1,3}(?:\.\d{3})*,\d{2}', valor_bruto).group(0)
            valor_num = float(valor_str_limpo.replace('.', '').replace(',', '.'))

            desc_limpa = linha.replace(data, '')
            for v in valores: desc_limpa = desc_limpa.replace(v, '')
            desc_limpa = re.sub(r'\b[DC]\b', '', desc_limpa, flags=re.IGNORECASE)
            desc_limpa = padronizar_texto(desc_limpa.strip())

            if eh_linha_de_saldo(desc_limpa):
                continue

            if not desc_limpa or len(desc_limpa) < 2: desc_limpa = "SEM DESCRICAO"

            desc_upper = desc_limpa.upper()
            if is_negativo:   sinal = '-'
            elif is_positivo: sinal = '+'
            else: sinal = '+' if any(w in desc_upper for w in ['ENTRADA', 'DEPOSITO', 'DEPÓSITO', 'RECEBIMENTO', 'CREDITO', 'CRÉDITO', 'PIX RECEBIDO', 'RESGATE']) else '-'

            dados.append({'Data': data, 'Descricao': desc_limpa, 'Valor': abs(valor_num), 'Sinal': sinal})
        elif len(linha) > 8:
            ignoradas_raw.append(linha)

    ignoradas_unicas    = list(dict.fromkeys(ignoradas_raw))
    ignoradas_com_valor = [l for l in ignoradas_unicas if re.search(r'\d,\d{2}', l)]
    ignoradas_texto     = [l for l in ignoradas_unicas if not re.search(r'\d,\d{2}', l)]

    return pd.DataFrame(dados), {"criticas": ignoradas_com_valor, "comuns": ignoradas_texto}

# ==========================================
# MOTOR ESPECÍFICO SICOOB (LÓGICA DE BLOCOS + TRAVA RIGOROSA)
# ==========================================
def processar_bloco_sicoob(bloco, ano, dados):
    texto_bloco = " ".join(bloco)
    
    match_data = re.search(r'^(\d{2}/\d{2})\b', texto_bloco)
    if not match_data: return
    data_ext = f"{match_data.group(1)}/{ano}"
    
    matches_valor = list(re.finditer(r'(\d{1,3}(?:\.\d{3})*,\d{2})\s*([CD]?)', texto_bloco, re.IGNORECASE))
    if not matches_valor: return
        
    match_v = matches_valor[0]
    valor_str = match_v.group(1)
    sinal_str = match_v.group(2).upper()
    
    valor_num = float(valor_str.replace('.', '').replace(',', '.'))
    
    desc_limpa = texto_bloco[match_data.end():].strip()
    
    for m in matches_valor:
        desc_limpa = desc_limpa.replace(m.group(0), ' ')
        
    desc_limpa = re.sub(r'\*+\.\d{3}\.\d{3}-\*+', '', desc_limpa) 
    desc_limpa = re.sub(r'\bDOC\.?:?\b', 'DOC', desc_limpa, flags=re.IGNORECASE)
    desc_limpa = re.sub(r'\b[CD]\b', '', desc_limpa) 
    desc_limpa = padronizar_texto(desc_limpa)
    
    if eh_linha_de_saldo(desc_limpa) or not desc_limpa: return
        
    if sinal_str == 'C': sinal = '+'
    elif sinal_str == 'D': sinal = '-'
    else:
        sinal = '-'
        if any(x in desc_limpa for x in ['RECEB', 'CREDIT', 'DEPOSIT', 'RESGATE', 'ESTORNO', 'DEVOLUCAO', 'PIX A FAVOR']):
            sinal = '+'
            
    dados.append({
        'Data': data_ext,
        'Descricao': desc_limpa,
        'Valor': abs(valor_num),
        'Sinal': sinal
    })

@st.cache_data(show_spinner=False)
def extrair_pdf_sicoob(file_bytes):
    dados, ignoradas_raw = [], []
    try:
        with pdfplumber.open(io.BytesIO(file_bytes)) as pdf:
            ano = str(pd.Timestamp.now().year)
            bloco_atual = []

            for page in pdf.pages:
                in_table = False 
                
                texto = page.extract_text()
                if not texto: continue
                
                match_ano = re.search(r'\b(20[2-9]\d)\b', texto)
                if match_ano: ano = match_ano.group(1)
                    
                linhas = texto.split('\n')
                
                for linha in linhas:
                    linha_strip = linha.strip()
                    linha_norm = padronizar_texto(linha_strip)
                    
                    if 'HISTORICO DE MOVIMENTACAO' in linha_norm or ('DATA' in linha_norm and 'HISTORICO' in linha_norm):
                        in_table = True
                        continue 
                        
                    if in_table and any(x in linha_norm for x in ['RESUMO', 'SALDOS', 'TOTAIS', 'OUVIDORIA', 'LANCAMENTOS FUTUROS']):
                        in_table = False
                        if bloco_atual:
                            processar_bloco_sicoob(bloco_atual, ano, dados)
                            bloco_atual = []
                        continue
                        
                    if not in_table:
                        continue
                        
                    if eh_linha_de_saldo(linha_norm):
                        if bloco_atual:
                            processar_bloco_sicoob(bloco_atual, ano, dados)
                            bloco_atual = []
                        continue
                        
                    if len(linha_strip) < 3: continue

                    match_data = re.search(r'^(\d{2}/\d{2})\b', linha_strip)
                    if match_data:
                        if bloco_atual: processar_bloco_sicoob(bloco_atual, ano, dados)
                        bloco_atual = [linha_strip]
                    elif bloco_atual:
                        bloco_atual.append(linha_strip)

            if bloco_atual:
                processar_bloco_sicoob(bloco_atual, ano, dados)

    except Exception as e: 
        logging.exception(f"Erro no Sicoob: {e}")
        
    return pd.DataFrame(dados), {"criticas": [], "comuns": ignoradas_raw}

# ==========================================
# MOTOR ESPECÍFICO ITAÚ
# ==========================================
@st.cache_data(show_spinner=False)
def extrair_pdf_itau(file_bytes):
    dados, ignoradas_raw = [], []
    try:
        with pdfplumber.open(io.BytesIO(file_bytes)) as pdf:
            in_movimento = False
            terminou_leitura = False
            current_date = None
            ano = str(pd.Timestamp.now().year)
            
            for page in pdf.pages:
                if terminou_leitura: break
                
                texto = page.extract_text()
                if not texto: continue
                
                match_ano = re.search(r'\b(20[2-9]\d)\b', texto)
                if match_ano:
                    ano = match_ano.group(1)

                linhas = texto.split('\n')
                
                for linha in linhas:
                    linha_strip = linha.strip()
                    linha_norm = padronizar_texto(linha_strip)
                    
                    if 'DATA' in linha_norm and 'DESCRICAO' in linha_norm and ('ENTRADA' in linha_norm or 'SAIDA' in linha_norm or 'CREDITO' in linha_norm):
                        in_movimento = True
                        continue
                    
                    if in_movimento:
                        if eh_linha_de_saldo(linha_norm):
                            if 'SALDO FINAL' in linha_norm or 'SDO FINAL' in linha_norm:
                                terminou_leitura = True
                                break
                            continue

                        if any(k in linha_norm for k in ['CHEQUE ESPECIAL', 'LIMITE']):
                            continue

                        match_data = re.search(r'^(\d{2}/\d{2})\b', linha_strip)
                        if match_data:
                            current_date = f"{match_data.group(1)}/{ano}"
                            linha_strip = linha_strip[match_data.end():].strip()

                        if not current_date:
                            continue

                        matches = list(re.finditer(r'(\d{1,3}(?:\.\d{3})*,\d{2})(-?)', linha_strip))
                        if matches:
                            v_match = None
                            if len(matches) >= 2:
                                m_last = matches[-1]
                                m_penult = matches[-2]
                                
                                distancia = m_last.start() - m_penult.end()
                                ta_no_fim = (len(linha_strip) - m_last.end()) <= 10
                                
                                if ta_no_fim and distancia <= 25:
                                    v_match = m_penult
                                    linha_strip = linha_strip[:m_last.start()].strip()
                                else:
                                    v_match = m_last
                            else:
                                v_match = matches[0]

                            valor_str = v_match.group(1)
                            sinal_str = v_match.group(2)
                            
                            sinal = '-' if sinal_str == '-' else '+'
                            valor_num = float(valor_str.replace('.', '').replace(',', '.'))
                            
                            desc = linha_strip[:v_match.start()].strip()
                            desc_limpa = padronizar_texto(desc)
                            
                            if desc_limpa and len(desc_limpa) >= 2:
                                dados.append({
                                    'Data': current_date,
                                    'Descricao': desc_limpa,
                                    'Valor': abs(valor_num),
                                    'Sinal': sinal
                                })
                            else:
                                ignoradas_raw.append(linha_strip)
                        else:
                            ignoradas_raw.append(linha_strip)
    except Exception as e:
        logging.exception(f"Erro no Itaú: {e}")
        
    return pd.DataFrame(dados), {"criticas": [], "comuns": ignoradas_raw}

# ==========================================
# MOTOR ESPECÍFICO CAIXA ECONÔMICA FEDERAL
# ==========================================
@st.cache_data(show_spinner=False)
def extrair_pdf_caixa(file_bytes):
    dados, ignoradas_raw = [], []
    try:
        with pdfplumber.open(io.BytesIO(file_bytes)) as pdf:
            for page in pdf.pages:
                texto = page.extract_text()
                if not texto: continue
                
                linhas = texto.split('\n')
                for linha in linhas:
                    linha_strip = linha.strip()
                    linha_norm = padronizar_texto(linha_strip)
                    
                    if 'EXTRATO' in linha_norm or 'DATA MOV' in linha_norm or 'NR. DOC.' in linha_norm:
                        continue
                        
                    if eh_linha_de_saldo(linha_norm):
                        continue

                    match = re.search(r'^(\d{2}/\d{2}/\d{4})\s+(.*?)\s+(\d{1,3}(?:\.\d{3})*,\d{2})\s*([CD])(?:\s+(\d{1,3}(?:\.\d{3})*,\d{2})\s*([CD]))?$', linha_strip, re.IGNORECASE)
                    
                    if match:
                        data = match.group(1)
                        meio = match.group(2)
                        valor_str = match.group(3)
                        sinal_str = match.group(4).upper()
                        
                        partes = meio.split(maxsplit=1)
                        if len(partes) > 1 and re.match(r'^\d+$', partes[0]):
                            desc_limpa = padronizar_texto(partes[1])
                        else:
                            desc_limpa = padronizar_texto(meio)
                            
                        if eh_linha_de_saldo(desc_limpa):
                            continue
                            
                        valor_num = float(valor_str.replace('.', '').replace(',', '.'))
                        sinal = '+' if sinal_str == 'C' else '-'
                        
                        if desc_limpa and len(desc_limpa) >= 2:
                            dados.append({
                                'Data': data,
                                'Descricao': desc_limpa,
                                'Valor': abs(valor_num),
                                'Sinal': sinal
                            })
                    else:
                        if len(linha_strip) > 5:
                            ignoradas_raw.append(linha_strip)
    except Exception as e:
        logging.exception(f"Erro na Caixa: {e}")
        
    return pd.DataFrame(dados), {"criticas": [], "comuns": ignoradas_raw}

# ==========================================
# MOTOR GENÉRICO OFX
# ==========================================
@st.cache_data(show_spinner=False)
def extrair_texto_ofx(file_bytes):
    dados_extraidos = []
    try:
        try:
            texto_ofx = file_bytes.decode('utf-8')
        except UnicodeDecodeError:
            texto_ofx = file_bytes.decode('latin-1', errors='ignore')

        blocos = re.findall(r'<STMTTRN>(.*?)</STMTTRN>', texto_ofx, re.DOTALL | re.IGNORECASE)
        if not blocos:
            blocos = re.split(r'<STMTTRN>', texto_ofx, flags=re.IGNORECASE)[1:]

        def get_campo(campo, texto):
            match = re.search(rf'<{campo}>\s*([^\n<]+)', texto, re.IGNORECASE)
            return match.group(1).strip() if match else ""

        for bloco in blocos:
            data_raw  = get_campo('DTPOSTED', bloco)
            valor_raw = get_campo('TRNAMT',   bloco)
            memo      = get_campo('MEMO',     bloco)
            name      = get_campo('NAME',     bloco)
            trntype   = get_campo('TRNTYPE',  bloco)
            fitid     = get_campo('FITID',    bloco)

            data_fmt = re.sub(r'[^\d]', '', data_raw)[:8]
            try:
                data_fmt = pd.to_datetime(data_fmt, format='%Y%m%d').strftime('%d/%m/%Y')
            except Exception:
                data_fmt = data_raw

            try:
                valor = float(valor_raw.replace(',', '.'))
            except (ValueError, AttributeError):
                continue

            VALORES_GENERICOS = {'', 'NONE', 'NULL', '-', 'N/A', 'NAO INFORMADO', 'NAO IDENTIFICADO'}
            name_pad = padronizar_texto(name)
            memo_pad = padronizar_texto(memo)

            partes = []
            if name_pad and name_pad not in VALORES_GENERICOS:
                partes.append(name_pad)
            if memo_pad and memo_pad not in VALORES_GENERICOS and memo_pad != name_pad:
                if name_pad not in memo_pad and memo_pad not in name_pad:
                    partes.append(memo_pad)
                elif len(memo_pad) > len(name_pad):
                    partes = [memo_pad]

            if not partes:
                partes.append(trntype.upper() if trntype else 'SEM DESCRICAO')

            descricao_final = " | ".join(partes) if partes else "SEM DESCRICAO"

            if eh_linha_de_saldo(descricao_final):
                continue

            dados_extraidos.append({
                'Data':      data_fmt,
                'Descricao': descricao_final,
                'Valor':     abs(valor),
                'Sinal':     '+' if valor > 0 else '-'
            })
    except Exception as e:
        logging.exception("Erro na extração OFX")
    return pd.DataFrame(dados_extraidos)

# ==========================================
# MOTOR ESPECÍFICO BANCO DO BRASIL
# ==========================================
@st.cache_data(show_spinner=False)
def extrair_planilha_bb(file_bytes, nome_arquivo):
    try:
        df_full = ler_planilha_robusto(file_bytes, nome_arquivo)
        if df_full.empty: return pd.DataFrame()
        
        header_idx = -1
        for idx, row in df_full.iterrows():
            row_str = padronizar_texto(" ".join([str(x) for x in row.values if pd.notna(x)]))
            if 'DATA' in row_str and 'VALOR' in row_str:
                header_idx = idx
                break
        
        if header_idx == -1: return pd.DataFrame()

        df_raw = df_full.iloc[header_idx+1:].copy()
        colunas_limpas = [padronizar_texto(str(c)) for c in df_full.iloc[header_idx].values]
        df_raw.columns = colunas_limpas
        
        col_data = next((c for c in colunas_limpas if 'DATA' in c), colunas_limpas[0])
        col_hist = next((c for c in colunas_limpas if 'HIST' in c and 'COD' not in c), None)
        col_valor = next((c for c in colunas_limpas if 'VALOR' in c), None)

        if not col_data or not col_valor: return pd.DataFrame()

        dados = []
        for _, row in df_raw.iterrows():
            data_raw = converter_data_excel(str(row[col_data]))
            if not data_raw: continue
            
            texto_historico = str(row[col_hist]).strip() if (col_hist and pd.notna(row[col_hist])) else ""
            if texto_historico.upper() == 'NAN': texto_historico = ""

            col_detalhe = next((c for c in colunas_limpas if any(w in c for w in ['DETALHE', 'COMPLEMENTO', 'DOCTO'])), None)
            texto_detalhe = str(row[col_detalhe]).strip() if (col_detalhe and pd.notna(row[col_detalhe])) else ""
            if texto_detalhe.upper() == 'NAN': texto_detalhe = ""
            
            desc_raw = f"{texto_historico} {texto_detalhe}".strip()
            desc_junta = re.sub(r'\d+', '', desc_raw) 
            desc_junta = padronizar_texto(re.sub(r'[^\w\s]', ' ', desc_junta).strip())
                
            if eh_linha_de_saldo(desc_junta): continue

            valor_bruto = str(row[col_valor]).upper().strip()
            if pd.isna(row[col_valor]) or valor_bruto == 'NAN' or valor_bruto == '': continue

            is_credit = False
            is_debit = False

            if 'C' in valor_bruto or '+' in valor_bruto: is_credit = True
            elif 'D' in valor_bruto or '-' in valor_bruto: is_debit = True

            valor_limpo = re.sub(r'[^\d.,]', '', valor_bruto)
            if not valor_limpo: continue

            if ',' in valor_limpo and '.' in valor_limpo: valor_limpo = valor_limpo.replace('.', '').replace(',', '.')
            elif ',' in valor_limpo: valor_limpo = valor_limpo.replace(',', '.')

            try: valor_num = float(valor_limpo)
            except ValueError: continue

            if valor_num == 0: continue
            if not is_credit and not is_debit: is_credit = True

            dados.append({
                'Data': data_raw,
                'Descricao': desc_junta if desc_junta else "SEM DESCRICAO",
                'Valor': abs(valor_num),
                'Sinal': '+' if is_credit else '-'
            })
            
        return pd.DataFrame(dados)
    except Exception as e:
        logging.exception(f"Erro BB: {e}")
        return pd.DataFrame()

# ==========================================
# MOTOR ESPECÍFICO BRADESCO (LÓGICA DINÂMICA)
# ==========================================
@st.cache_data(show_spinner=False)
def extrair_planilha_bradesco(file_bytes, nome_arquivo):
    try:
        df_full = ler_planilha_robusto(file_bytes, nome_arquivo)
        if df_full.empty: return pd.DataFrame()
        
        header_idx = -1
        for idx, row in df_full.iterrows():
            row_str = padronizar_texto(" ".join([str(x) for x in row.values if pd.notna(x)]))
            if 'DATA' in row_str and any(w in row_str for w in ['LANCAM', 'HIST', 'DESCR', 'DCTO', 'DOCTO']):
                header_idx = idx
                break
        
        if header_idx == -1: return pd.DataFrame()

        df_raw = df_full.iloc[header_idx+1:].copy()
        colunas_limpas = [padronizar_texto(str(c)) for c in df_full.iloc[header_idx].values]
        
        colunas_unicas = []
        for i, col in enumerate(colunas_limpas):
            base = col if col and col != 'NAN' else f"COLUNA_VAZIA_{i}"
            if base in colunas_unicas: base = f"{base}_{i}"
            colunas_unicas.append(base)
            
        df_raw.columns = colunas_unicas
        
        col_data = next((c for c in colunas_unicas if 'DATA' in c), colunas_unicas[0])
        col_lanc = next((c for c in colunas_unicas if any(w in c for w in ['LANCAM', 'HIST', 'DESCR'])), colunas_unicas[1])
        col_cred = next((c for c in colunas_unicas if 'CRED' in c), None)
        col_deb  = next((c for c in colunas_unicas if 'DEB' in c), None)
        col_valor = next((c for c in colunas_unicas if 'VALOR' in c), None)
        
        dados = []
        for idx, row in df_raw.iterrows():
            check_end = str(row[col_data]) + " " + str(row[col_lanc])
            if 'TOTAL' in check_end.upper() or 'SALDO' in check_end.upper(): break
                
            data_val = converter_data_excel(str(row[col_data]))
            if not data_val: continue
                
            desc = padronizar_texto(str(row[col_lanc]).strip())
            if eh_linha_de_saldo(desc): continue
                
            valor_final = 0.0
            sinal_final = '+'
            
            if col_cred and col_deb:
                v_cred = str(row[col_cred]).strip() if pd.notna(row[col_cred]) else ""
                v_deb = str(row[col_deb]).strip() if pd.notna(row[col_deb]) else ""

                if v_cred and v_cred.upper() != 'NAN' and v_cred != '0':
                    v_clean = re.sub(r'[^\d.,]', '', v_cred)
                    if ',' in v_clean and '.' in v_clean: v_clean = v_clean.replace('.', '').replace(',', '.')
                    elif ',' in v_clean: v_clean = v_clean.replace(',', '.')
                    try:
                        valor_final = float(v_clean)
                        sinal_final = '+'
                    except ValueError: pass
                elif v_deb and v_deb.upper() != 'NAN' and v_deb != '0':
                    v_clean = re.sub(r'[^\d.,]', '', v_deb) 
                    if ',' in v_clean and '.' in v_clean: v_clean = v_clean.replace('.', '').replace(',', '.')
                    elif ',' in v_clean: v_clean = v_clean.replace(',', '.')
                    try:
                        valor_final = float(v_clean)
                        sinal_final = '-'
                    except ValueError: pass

            elif col_valor:
                v_val = str(row[col_valor]).strip()
                if v_val and v_val.upper() != 'NAN':
                    is_neg = '-' in v_val or 'D' in v_val.upper()
                    v_clean = re.sub(r'[^\d.,]', '', v_val)
                    if ',' in v_clean and '.' in v_clean: v_clean = v_clean.replace('.', '').replace(',', '.')
                    elif ',' in v_clean: v_clean = v_clean.replace(',', '.')
                    try:
                        valor_final = float(v_clean)
                        sinal_final = '-' if is_neg else '+'
                    except ValueError: pass
            
            if valor_final > 0:
                dados.append({
                    'Data': data_val,
                    'Descricao': desc,
                    'Valor': abs(valor_final),
                    'Sinal': sinal_final
                })
                
        return pd.DataFrame(dados)
    except Exception as e:
        logging.exception(f"Erro Bradesco: {e}")
        return pd.DataFrame()

# =============================================================================
# 4. CARGA DE DADOS DO BANCO E DE REGRAS
# =============================================================================
@st.cache_data(ttl=60, show_spinner=False)
def carregar_empresas():
    conn = None
    try:
        conn = get_connection()
        df = pd.read_sql("SELECT id, nome, fantasia, cnpj, tipo, apelido_unidade, conta_contabil FROM empresas", conn)
        df['tipo']         = df['tipo'].fillna('Matriz')
        df['cnpj']         = df['cnpj'].fillna('Sem CNPJ')
        df['cnpj_limpo']   = df['cnpj'].astype(str).apply(limpar_cnpj)
        df['display_nome'] = df['nome'] + ' | ' + df['tipo'] + ' | ' + df['cnpj']
        return df
    except mysql.connector.Error: return pd.DataFrame()
    finally:
        if conn: conn.close()

@st.cache_data(ttl=60, show_spinner=False)
def carregar_contas_por_banco(id_empresa):
    conn = None
    try:
        conn = get_connection()
        return pd.read_sql("SELECT id, nome_banco, conta_contabil FROM empresa_banco_contas WHERE id_empresa = %s ORDER BY nome_banco", conn, params=(id_empresa,))
    except mysql.connector.Error: return pd.DataFrame()
    finally:
        if conn: conn.close()

# =============================================================================
# 5. INTERFACE PRINCIPAL
# =============================================================================
st.set_page_config(page_title="Conciliação Bancária", page_icon="🏦", layout="wide")

defaults = {
    'skipped_indices':         [],
    'editando_regra_id':       None,
    'editando_conta_banco_id': None,
    'df_bruto':                pd.DataFrame(),
    'banco_detectado':         "DESCONHECIDO",
    'empresa_detectada_data':  None,
    'prontos':                 [],
    'pendentes':               pd.DataFrame(),
    'linhas_ignoradas_regras': [],
    'criticas':                [],
    'comuns':                  [],
    'undo_stack':              [],
    'busca_fila':              '',
    'inicio_operacao':         None,
    'tempo_conclusao':         None,
    'lancamentos_manuais':     [],
}
for key, val in defaults.items():
    if key not in st.session_state:
        st.session_state[key] = val

st.title("🏦 Conciliação Bancária")

df_empresas = carregar_empresas()
if df_empresas.empty:
    st.error("Nenhuma empresa ativa encontrada no banco de dados.")
    st.stop()

# =============================================================================
# PASSO 1 E 2: UPLOAD E PRÉ-SELEÇÃO
# =============================================================================
uploaded_files  = st.file_uploader("1. Arraste seus extratos (PDF, OFX, XLS, XLSX, CSV)", type=["pdf", "ofx", "xls", "xlsx", "csv"], accept_multiple_files=True)
indice_sugerido = 0

if uploaded_files:
    mensagens_auto = []
    for file in uploaded_files:
        if file.name.lower().endswith('.pdf'):
            cnpj_lido = identificar_cnpj_no_pdf(file.getvalue())
            if cnpj_lido:
                empresa_detectada_data = buscar_empresa_por_cnpj_otimizado(cnpj_lido, df_empresas)
                if empresa_detectada_data:
                    idx_encontrado  = df_empresas[df_empresas['id'] == empresa_detectada_data['id']].index[0]
                    indice_sugerido = int(idx_encontrado)
                    st.session_state.empresa_detectada_data = empresa_detectada_data
                    if f"Empresa '{empresa_detectada_data['nome']}' reconhecida" not in mensagens_auto:
                        mensagens_auto.append(f"Empresa '{empresa_detectada_data['nome']}' reconhecida pelo CNPJ")

            banco_detectado = identificar_banco_no_pdf(file.getvalue())
            if banco_detectado != "DESCONHECIDO":
                st.session_state.banco_detectado = banco_detectado
                if f"Banco '{banco_detectado}'" not in mensagens_auto:
                    mensagens_auto.append(f"Banco '{banco_detectado}' identificado")
            break
            
    if mensagens_auto:
        st.success("✅ " + " | ".join(mensagens_auto))

# =============================================================================
# PASSO 3: PAINEL DE CONFIGURAÇÕES
# =============================================================================
st.markdown("### 2. Confirme os Dados")
col_cfg1, col_cfg2, col_cfg3 = st.columns([2, 1, 1])

empresa_sel_display = col_cfg1.selectbox("Empresa / Filial", df_empresas['display_nome'], index=indice_sugerido)
empresa_data        = df_empresas[df_empresas['display_nome'] == empresa_sel_display].iloc[0].to_dict()
id_empresa          = int(empresa_data['id'])

bancos_disponiveis = sorted(list(BANCOS_KEYWORDS.keys()) + [st.session_state.banco_detectado])
bancos_disponiveis = list(dict.fromkeys([b for b in bancos_disponiveis if b != "DESCONHECIDO"]))
banco_index        = (bancos_disponiveis.index(st.session_state.banco_detectado) if st.session_state.banco_detectado in bancos_disponiveis else 0)
banco_selecionado  = col_cfg2.selectbox("Banco do Extrato", bancos_disponiveis, index=banco_index)

conta_banco_fixa = buscar_conta_por_banco(id_empresa, banco_selecionado)
if not conta_banco_fixa:
    conta_banco_fixa = empresa_data.get('conta_contabil', 'N/A')

col_cfg2.text_input("Conta Banco (Âncora)", value=conta_banco_fixa, disabled=True)

col_saldos1, col_saldos2 = col_cfg3.columns(2)
saldo_anterior_informado = col_saldos1.number_input("Saldo Anterior (R$)", value=0.00, step=100.00, format="%.2f")
saldo_final_informado = col_saldos2.number_input("Saldo Final (Opcional)", value=0.00, step=100.00, format="%.2f", help="Informe o saldo final real do extrato para checagem do sistema.")

# =============================================================================
# PASSO 4: PROCESSAMENTO
# =============================================================================
if uploaded_files and conta_banco_fixa != 'N/A':
    if st.button("⚙️ Processar Extratos"):
        
        st.session_state.inicio_operacao = time.time()
        st.session_state.tempo_conclusao = None
        st.session_state.lancamentos_manuais = [] # Reseta os manuais num novo processamento
        st.session_state.linhas_ignoradas_regras = [] # Reseta as exclusões manuais
        
        with st.spinner("Lendo e classificando extratos..."):
            lista_dfs, criticas, comuns = [], [], []
            for file in uploaded_files:
                extensao = file.name.lower()
                
                if extensao.endswith('.pdf'):
                    banco_pdf = identificar_banco_no_pdf(file.getvalue())
                    if banco_pdf == 'CAIXA' or banco_selecionado == 'CAIXA':
                        df_ex, ign = extrair_pdf_caixa(file.getvalue())
                    elif banco_pdf == 'SICOOB' or banco_selecionado == 'SICOOB':
                        df_ex, ign = extrair_pdf_sicoob(file.getvalue())
                    elif banco_pdf == 'ITAU' or banco_selecionado == 'ITAU':
                        df_ex, ign = extrair_pdf_itau(file.getvalue())
                    else:
                        df_ex, ign = extrair_por_recintos(file.getvalue())
                    
                    if not df_ex.empty:
                        lista_dfs.append(df_ex)
                        criticas.extend(ign['criticas'])
                        comuns.extend(ign['comuns'])
                    else:
                        st.warning(f"⚠️ Extrator PDF não encontrou transações em: {file.name}")
                        
                elif extensao.endswith(('.xlsx', '.xls', '.csv')):
                    if banco_selecionado == 'BRADESCO':
                        df_ex = extrair_planilha_bradesco(file.getvalue(), file.name)
                    else:
                        df_ex = extrair_planilha_bb(file.getvalue(), file.name)
                        
                    if not df_ex.empty:
                        lista_dfs.append(df_ex)
                    else:
                        st.warning(f"⚠️ Extrator Excel/CSV não encontrou transações na planilha: {file.name}")
                        
                elif extensao.endswith('.ofx'):
                    df_ex = extrair_texto_ofx(file.getvalue())
                    if not df_ex.empty:
                        lista_dfs.append(df_ex)
                    else:
                        st.warning(f"⚠️ Extrator OFX não encontrou transações em: {file.name}")

            if lista_dfs:
                df_consolidado = pd.concat(lista_dfs, ignore_index=True)
                df_consolidado['Valor'] = pd.to_numeric(df_consolidado['Valor'], errors='coerce').fillna(0.0)
                df_consolidado['Sinal'] = df_consolidado['Sinal'].astype(str).apply(lambda x: '+' if '+' in x else '-')
                st.session_state.df_bruto = df_consolidado
            else:
                st.session_state.df_bruto = pd.DataFrame()
                
            st.session_state.skipped_indices = []
            st.session_state.criticas        = criticas
            st.session_state.comuns          = comuns
            st.session_state.busca_fila      = ''
            undo_manager.clear()

            aplicar_regras_aos_extratos(st.session_state.df_bruto, id_empresa, banco_selecionado, conta_banco_fixa)
            st.success("Processamento concluído!")
            st.rerun()
elif conta_banco_fixa == 'N/A':
    st.error("Configure a conta contábil antes de processar.")

# =============================================================================
# PASSO 5: RESULTADOS + DETETIVE + AUDITORIA + INCLUSÕES/EXCLUSÕES
# =============================================================================
if not st.session_state.df_bruto.empty:
    st.divider()

    df_validos = st.session_state.df_bruto[
        ~st.session_state.df_bruto.index.isin(st.session_state.linhas_ignoradas_regras)
    ]
    
    total_e               = float(df_validos[df_validos['Sinal'] == '+']['Valor'].sum())
    total_s               = float(df_validos[df_validos['Sinal'] == '-']['Valor'].sum())
    
    # Soma dos manuais na visualização
    if 'lancamentos_manuais' in st.session_state and st.session_state.lancamentos_manuais:
        for m_item in st.session_state.lancamentos_manuais:
            val_m = float(m_item['Valor'].replace(',', '.'))
            if m_item['Debito'] == conta_banco_fixa: # Entrada
                total_e += val_m
            else:
                total_s += val_m

    saldo_final_calculado = saldo_anterior_informado + total_e - total_s

    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Saldo Anterior",           formatar_moeda(saldo_anterior_informado))
    c2.metric("🟢 Entradas Válidas",      formatar_moeda(total_e))
    c3.metric("🔴 Saídas Válidas",        formatar_moeda(total_s))
    c4.metric("⚖️ Saldo Final Calculado", formatar_moeda(saldo_final_calculado))

    # O DETETIVE
    if saldo_final_informado != 0.00:
        diferenca = round(abs(saldo_final_calculado - saldo_final_informado), 2)
        if diferenca > 0.01:
            st.error(f"⚠️ **Atenção!** Há uma diferença de **{formatar_moeda(diferenca)}** entre o saldo calculado e o que você informou.")
            
            encontrou_pista = False
            suspeitos_bruto = st.session_state.df_bruto[st.session_state.df_bruto['Valor'] == diferenca]
            if not suspeitos_bruto.empty:
                st.info(f"💡 **PISTA 1:** Encontrei {len(suspeitos_bruto)} lançamento(s) na fila com o valor exato da diferença. Pode ser que um deles devesse ter sido ignorado ou o sinal esteja errado.")
                encontrou_pista = True
                
            metade = round(diferenca / 2, 2)
            suspeitos_metade = st.session_state.df_bruto[st.session_state.df_bruto['Valor'] == metade]
            if not suspeitos_metade.empty:
                st.info(f"💡 **PISTA 2:** Há um lançamento na fila de **{formatar_moeda(metade)}**. Se o sinal dele estiver invertido, ele gera exatamente essa diferença!")
                encontrou_pista = True

            str_diff_br = f"{diferenca:.2f}".replace('.', ',')
            suspeitos_lixo = [l for l in st.session_state.criticas if str_diff_br in l]
            if suspeitos_lixo:
                st.info(f"💡 **PISTA 3:** O valor de {formatar_moeda(diferenca)} aparece nas linhas que o sistema ignorou. Talvez um lançamento válido tenha se perdido por falta de cabeçalho. Vá na auditoria abaixo e verifique!")
                encontrou_pista = True

            if not encontrou_pista:
                st.info("💡 **PISTA:** Não encontrei um culpado exato. Essa diferença deve ser a soma de múltiplos lançamentos que faltaram ou vieram a mais.")
        else:
            st.success("✅ **O Saldo Final Calculado bateu perfeitamente com o Saldo Final Informado!**")

    # ==========================================
    # INCLUSÃO MANUAL 
    # ==========================================
    with st.expander("➕ Adicionar Lançamento Manual (Ajuste de Saldo)", expanded=False):
        st.caption("Se o PDF pular alguma linha ou você precisar forçar uma compensação, adicione o lançamento aqui. Ele vai direto para o arquivo final.")
        col_m1, col_m2, col_m3 = st.columns([1, 2, 1])
        m_data = col_m1.text_input("Data (DD/MM/AAAA)")
        m_desc = col_m2.text_input("Descrição do Lançamento")
        m_valor = col_m3.number_input("Valor (R$)", step=0.01, format="%.2f")
        
        col_m4, col_m5, col_m6 = st.columns([1, 1, 2])
        m_sinal = col_m4.selectbox("Tipo", ["- (Saída)", "+ (Entrada)"])
        m_conta = col_m5.text_input("Conta Contrapartida")
        
        if st.button("Adicionar Lançamento Manual à Exportação", type="primary"):
            if m_data and m_desc and m_valor > 0 and m_conta:
                sinal_final = '+' if '+' in m_sinal else '-'
                novo_item = {
                    'idx_original':  f"manual_{uuid.uuid4().hex}", # Gera ID único para exclusão
                    'Debito':  conta_banco_fixa if sinal_final == '+' else m_conta,
                    'Credito': m_conta if sinal_final == '+' else conta_banco_fixa,
                    'Data':    m_data,
                    'Valor':   f"{m_valor:.2f}".replace('.', ','),
                    'Cod_Historico': "",
                    'Historico': m_desc.upper()
                }
                if 'lancamentos_manuais' not in st.session_state:
                    st.session_state.lancamentos_manuais = []
                st.session_state.lancamentos_manuais.append(novo_item)
                
                # Reaplica as regras para colocar a inclusão manual na lista final
                aplicar_regras_aos_extratos(st.session_state.df_bruto, id_empresa, banco_selecionado, conta_banco_fixa)
                st.toast("Lançamento manual inserido com sucesso!")
                st.rerun()
            else:
                st.error("Preencha Data, Descrição, Valor maior que zero e Conta de Contrapartida.")

    # ==========================================
    # EXCLUSÃO ESPECÍFICA DE LANÇAMENTOS PRONTOS
    # ==========================================
    with st.expander("🗑️ Excluir Lançamento Específico do Arquivo Final", expanded=False):
        st.caption("Abaixo estão os lançamentos que **já estão prontos** para exportar. Selecione os que deseja remover permanentemente (por duplicidade ou erro).")
        
        if st.session_state.prontos:
            opcoes_exclusao = []
            for p in st.session_state.prontos:
                # Cria a string visual pro menu. Fica assim: [15] 10/04/2026 - R$ 150,00 - TARIFA
                opcoes_exclusao.append(f"[{p['idx_original']}] {p['Data']} | R$ {p['Valor']} | {p['Historico']}")
                
            itens_para_excluir = st.multiselect("Lançamentos para remover:", opcoes_exclusao)
            
            if st.button("❌ Remover Lançamentos Selecionados"):
                for item in itens_para_excluir:
                    idx_str = item.split(']')[0][1:] # Extrai o ID de dentro dos colchetes
                    
                    if str(idx_str).startswith('manual_'):
                        # Exclui da lista de manuais
                        st.session_state.lancamentos_manuais = [m for m in st.session_state.lancamentos_manuais if m['idx_original'] != idx_str]
                    else:
                        # Exclui do bruto adicionando o index original na lista de ignorados
                        if int(idx_str) not in st.session_state.linhas_ignoradas_regras:
                            st.session_state.linhas_ignoradas_regras.append(int(idx_str))
                
                # Roda a inteligência de novo para atualizar as listas
                aplicar_regras_aos_extratos(st.session_state.df_bruto, id_empresa, banco_selecionado, conta_banco_fixa)
                st.toast("Lançamentos removidos com sucesso!")
                st.rerun()
        else:
            st.info("Nenhum lançamento processado na fila de exportação.")

    # PAINEL OCULTO DE AUDITORIA
    with st.expander("🔍 Auditoria: Ver tudo que foi Lido e Ignorado (Bruto)"):
        st.markdown("### 📊 Dados Lidos e Capturados")
        st.dataframe(st.session_state.df_bruto, use_container_width=True)
        
        st.markdown("### 🗑️ Linhas Ignoradas (Lixo)")
        if st.session_state.criticas or st.session_state.comuns:
            if st.session_state.criticas:
                st.error("Linhas descartadas que possuíam valores (Podem conter erros de leitura):")
                for l in list(dict.fromkeys(st.session_state.criticas)): st.code(l)
            if st.session_state.comuns:
                st.info("Linhas de texto descartadas (Ruído de cabeçalho):")
                for l in list(dict.fromkeys(st.session_state.comuns))[:30]: st.text(l)
        else:
            st.write("Nenhuma linha foi descartada.")

    # =========================================================================
    # MESA DE TREINAMENTO
    # =========================================================================
    df_p = st.session_state.pendentes
    fila = df_p[~df_p['idx_original'].isin(st.session_state.skipped_indices)] if not df_p.empty else pd.DataFrame()

    if not fila.empty:
        st.subheader("🎓 Mesa de Treinamento")

        col_busca, col_limpar, col_total = st.columns([3, 1, 1])
        busca_fila = col_busca.text_input("🔍 Buscar na fila (opcional)", value=st.session_state.busca_fila)
        st.session_state.busca_fila = busca_fila

        if col_limpar.button("✖ Limpar busca", disabled=not busca_fila):
            st.session_state.busca_fila = ''
            st.rerun()

        fila_filtrada = fila
        if busca_fila.strip():
            termo_busca   = padronizar_texto(busca_fila.strip())
            fila_filtrada = fila[fila['Descricao'].str.contains(re.escape(termo_busca), case=False, na=False)]

        col_total.metric("📋 Pendentes", f"{len(fila_filtrada)} / {len(fila)}" if busca_fila.strip() else len(fila))

        if fila_filtrada.empty:
            st.warning("Nenhum lançamento encontrado.")
        else:
            item = fila_filtrada.iloc[0]

            m1, m2, m3, m4, m5 = st.columns([1, 1, 1, 3, 1])
            m1.metric("📅 Data",  item['Data'])
            m2.metric("💰 Valor", formatar_moeda(item['Valor']))
            m3.metric("↕️ Tipo",  "🟢 Entrada" if item['Sinal'] == '+' else "🔴 Saída")
            m4.write(f"**Descrição Extraída:** {item['Descricao']}")

            if not undo_manager.is_empty():
                if m5.button("↩️ Desfazer Ação", type="primary"):
                    ultima_acao = undo_manager.pop()
                    conn = None
                    try:
                        conn = get_connection()
                        cursor = conn.cursor()
                        t, d   = ultima_acao['type'], ultima_acao['data']

                        if t in ('salvar_regra', 'ignorar_lixo'):
                            cursor.execute("DELETE FROM tb_extratos_regras WHERE id = %s", (d['id_regra'],))
                            conn.commit()
                            aplicar_regras_aos_extratos(st.session_state.df_bruto, id_empresa, banco_selecionado, conta_banco_fixa)
                            st.toast("Ação desfeita!")
                        elif t == 'pular':
                            if d['idx'] in st.session_state.skipped_indices:
                                st.session_state.skipped_indices.remove(d['idx'])
                    except mysql.connector.Error as err: st.error(f"Erro: {err}")
                    finally:
                        if conn: conn.close()
                    st.rerun()

            with st.expander("🛠️ Corrigir Leitura (Caso o extrator tenha confundido saldo/valor/texto)", expanded=False):
                st.caption("Altere os dados abaixo e clique em Salvar para corrigir este lançamento definitivamente na fila.")
                ce1, ce2, ce3 = st.columns([3, 1, 1])
                nova_desc = ce1.text_input("Descrição Correta", value=item['Descricao'], key=f"nd_{item['idx_original']}")
                novo_val = ce2.number_input("Valor Correto", value=float(item['Valor']), step=0.01, format="%.2f", key=f"nv_{item['idx_original']}")
                novo_sin = ce3.selectbox("Sinal Correto", ['+', '-'], index=0 if item['Sinal']=='+' else 1, key=f"ns_{item['idx_original']}")
                
                if st.button("💾 Aplicar Correção Permanente", type="primary"):
                    st.session_state.df_bruto.at[item['idx_original'], 'Descricao'] = nova_desc
                    st.session_state.df_bruto.at[item['idx_original'], 'Valor'] = float(novo_val)
                    st.session_state.df_bruto.at[item['idx_original'], 'Sinal'] = novo_sin
                    aplicar_regras_aos_extratos(st.session_state.df_bruto, id_empresa, banco_selecionado, conta_banco_fixa)
                    st.toast("Lançamento corrigido com sucesso!")
                    st.rerun()

            palavras_desc = item['Descricao'].split()
            selecionadas  = st.pills("Selecione os termos-chave:", palavras_desc, selection_mode="multi")
            termo_final = " ".join(selecionadas) if selecionadas else item['Descricao']

            if termo_final:
                palavras_busca = termo_final.split()
                mascara = pd.Series([True] * len(df_p), index=df_p.index)
                for palavra in palavras_busca:
                    mascara &= df_p['Descricao'].str.contains(re.escape(palavra), case=False, na=False)
                
                df_impactados = df_p[mascara]
                impacto = len(df_impactados)
                
                if impacto > 0:
                    with st.expander(f"🎯 Visão de Raio-X: Esta regra vai automatizar {impacto} operação(ões) pendente(s).", expanded=False):
                        st.dataframe(
                            df_impactados[['Data', 'Descricao', 'Valor', 'Sinal']].style.format({"Valor": "R$ {:.2f}"}),
                            use_container_width=True,
                            hide_index=True
                        )

            with st.form("form_treino"):
                f1, f2, f3 = st.columns(3)
                contra = f1.text_input("Contrapartida (Conta Contábil)")
                cod_h  = f2.text_input("Cód. Hist. Alterdata (Opcional)")
                txt_h  = f3.text_input("Histórico Padrão (Opcional)")
                b1, b2, b3, b4 = st.columns(4)

                if b1.form_submit_button("✅ Salvar Regra"):
                    if contra:
                        conn = None
                        try:
                            cod_h_val = cod_h if cod_h.strip() else None
                            txt_h_val = txt_h if txt_h.strip() else None

                            conn = get_connection()
                            cursor = conn.cursor()
                            cursor.execute(
                                "INSERT INTO tb_extratos_regras (id_empresa, banco_nome, termo_chave, sinal_esperado, conta_contabil, cod_historico_erp, historico_padrao) VALUES (%s,%s,%s,%s,%s,%s,%s)",
                                (id_empresa, banco_selecionado, termo_final, item['Sinal'], contra, cod_h_val, txt_h_val)
                            )
                            id_inserido = cursor.lastrowid
                            conn.commit()
                            undo_manager.push('salvar_regra', {'id_regra': id_inserido})
                            aplicar_regras_aos_extratos(st.session_state.df_bruto, id_empresa, banco_selecionado, conta_banco_fixa)
                            st.success("Regra salva!")
                        except mysql.connector.Error as err: st.error(f"Erro ao salvar regra: {err}")
                        finally:
                            if conn: conn.close()
                        st.rerun()
                    else:
                        st.error("Preencha a conta de contrapartida.")

                if b2.form_submit_button("🗑️ Ignorar Lixo"):
                    conn = None
                    try:
                        conn = get_connection()
                        cursor = conn.cursor()
                        cursor.execute(
                            "INSERT INTO tb_extratos_regras (id_empresa, banco_nome, termo_chave, sinal_esperado, conta_contabil) VALUES (%s,%s,%s,%s,%s)",
                            (id_empresa, banco_selecionado, termo_final, item['Sinal'], 'IGNORAR')
                        )
                        id_inserido = cursor.lastrowid
                        conn.commit()
                        undo_manager.push('ignorar_lixo', {'id_regra': id_inserido})
                        aplicar_regras_aos_extratos(st.session_state.df_bruto, id_empresa, banco_selecionado, conta_banco_fixa)
                        st.success("Lançamento ignorado!")
                    except mysql.connector.Error as err: st.error(f"Erro ao ignorar: {err}")
                    finally:
                        if conn: conn.close()
                    st.rerun()

                if b3.form_submit_button("⏭️ Pular"):
                    st.session_state.skipped_indices.append(item['idx_original'])
                    undo_manager.push('pular', {'idx': item['idx_original']})
                    st.rerun()

                if b4.form_submit_button("🔄 Resetar Fila"):
                    st.session_state.skipped_indices = []
                    st.session_state.busca_fila      = ''
                    undo_manager.clear()
                    st.rerun()
    else:
        if st.session_state.inicio_operacao is not None and st.session_state.tempo_conclusao is None:
            st.session_state.tempo_conclusao = time.time() - st.session_state.inicio_operacao
            
        st.success("🎉 Todos os lançamentos pendentes foram mapeados! Exportação liberada.")
        
        if st.session_state.tempo_conclusao is not None:
            minutos = int(st.session_state.tempo_conclusao // 60)
            segundos = int(st.session_state.tempo_conclusao % 60)
            st.info(f"⏱️ **Produtividade:** Operação concluída em {minutos} minuto(s) e {segundos} segundo(s).")

        if st.session_state.prontos:
            # 1. Transformar em DataFrame
            df_prontos = pd.DataFrame(st.session_state.prontos)
            
            # 2. Retirar a coluna de ID interno que usamos só no Streamlit
            if 'idx_original' in df_prontos.columns:
                df_prontos = df_prontos.drop(columns=['idx_original'])
                
            # 3. Adicionar a coluna vazia ANTES da coluna 'Debito'
            if 'Debito' in df_prontos.columns:
                idx_debito = df_prontos.columns.get_loc('Debito')
                df_prontos.insert(idx_debito, '', '') # O cabeçalho ficará vazio

            # Exportação direto em XLSX via buffer na memória
            output = io.BytesIO()
            with pd.ExcelWriter(output, engine='xlsxwriter') as writer:
                df_prontos.to_excel(writer, index=False, sheet_name='Conciliacao')
            
            st.download_button(
                label="📥 BAIXAR EXCEL PARA ERP",
                data=output.getvalue(),
                file_name=f"conciliacao_{empresa_data['apelido_unidade']}_{banco_selecionado}_{pd.Timestamp.now().strftime('%Y%m%d%H%M%S')}.xlsx",
                mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
            )

# =============================================================================
# GERENCIAMENTO DE REGRAS E CONTAS
# =============================================================================
st.divider()
with st.expander("📚 Gerenciar Regras Cadastradas", expanded=False):
    conn = None
    try:
        conn = get_connection()
        regras_v = pd.read_sql(
            "SELECT * FROM tb_extratos_regras WHERE id_empresa = %s AND banco_nome = %s ORDER BY id DESC",
            conn, params=(id_empresa, banco_selecionado)
        )

        busca_regra = st.text_input("🔍 Buscar por termo chave ou conta contábil...", key="busca_regra")
        if busca_regra:
            regras_v = regras_v[
                regras_v['termo_chave'].str.contains(busca_regra, case=False, na=False) |
                regras_v['conta_contabil'].str.contains(busca_regra, case=False, na=False)
            ]

        if not regras_v.empty:
            for _, r in regras_v.iterrows():
                col_r = st.columns([3, 2, 1, 1])
                col_r[0].write(f"**{r['termo_chave']}**")
                col_r[1].write(f"Conta: {r['conta_contabil']} ({r['sinal_esperado']})")
                if col_r[2].button("✏️", key=f"e_regra_{r['id']}"):
                    st.session_state.editando_regra_id = r['id']
                    st.rerun()
                if col_r[3].button("🗑️", key=f"d_regra_{r['id']}"):
                    cursor = conn.cursor()
                    cursor.execute("DELETE FROM tb_extratos_regras WHERE id = %s", (int(r['id']),))
                    conn.commit()
                    aplicar_regras_aos_extratos(st.session_state.df_bruto, id_empresa, banco_selecionado, conta_banco_fixa)
                    st.toast("Regra deletada!")
                    st.rerun()
        else:
            st.info("Nenhuma regra encontrada para este banco e empresa.")
    except mysql.connector.Error as err:
        st.error(f"Erro ao carregar regras: {err}")
    finally:
        if conn: conn.close()

    if st.session_state.editando_regra_id and not regras_v.empty:
        linha_editar = regras_v[regras_v['id'] == st.session_state.editando_regra_id]
        if not linha_editar.empty:
            regra_para_editar = linha_editar.iloc[0]
            st.subheader(f"Editar Regra ID: {regra_para_editar['id']}")
            with st.form(key=f"form_editar_regra_{regra_para_editar['id']}"):
                col_er1, col_er2 = st.columns(2)
                novo_termo    = col_er1.text_input("Termo Chave",    value=regra_para_editar['termo_chave'])
                nova_conta    = col_er2.text_input("Conta Contábil", value=regra_para_editar['conta_contabil'])
                novo_sinal    = st.selectbox("Sinal", ['+', '-'], index=0 if regra_para_editar['sinal_esperado'] == '+' else 1)
                novo_cod_hist = st.text_input("Cód. Histórico Alterdata", value=str(regra_para_editar.get('cod_historico_erp', '') or ''))
                novo_hist     = st.text_area("Histórico Padrão",    value=str(regra_para_editar.get('historico_padrao', '') or ''))
                if st.form_submit_button("Salvar Edição"):
                    conn = None
                    try:
                        novo_cod_hist_val = novo_cod_hist if novo_cod_hist.strip() else None
                        novo_hist_val = novo_hist if novo_hist.strip() else None
                        
                        conn = get_connection()
                        cursor = conn.cursor()
                        cursor.execute(
                            "UPDATE tb_extratos_regras SET termo_chave=%s, conta_contabil=%s, sinal_esperado=%s, cod_historico_erp=%s, historico_padrao=%s WHERE id=%s",
                            (novo_termo, nova_conta, novo_sinal, novo_cod_hist_val, novo_hist_val, regra_para_editar['id'])
                        )
                        conn.commit()
                        st.session_state.editando_regra_id = None
                        aplicar_regras_aos_extratos(st.session_state.df_bruto, id_empresa, banco_selecionado, conta_banco_fixa)
                        st.success("Regra atualizada!")
                    except mysql.connector.Error as err: st.error(f"Erro ao atualizar: {err}")
                    finally:
                        if conn: conn.close()
                    st.rerun()
                if st.form_submit_button("Cancelar"):
                    st.session_state.editando_regra_id = None
                    st.rerun()

st.divider()
with st.expander("📊 Gerenciar Contas Contábeis por Banco", expanded=False):
    df_contas_banco = carregar_contas_por_banco(id_empresa)

    st.subheader("Contas Cadastradas")
    if not df_contas_banco.empty:
        for _, c in df_contas_banco.iterrows():
            col_c = st.columns([2, 2, 1, 1])
            col_c[0].write(f"**Banco:** {c['nome_banco']}")
            col_c[1].write(f"**Conta Contábil:** {c['conta_contabil']}")
            if col_c[2].button("✏️", key=f"e_conta_{c['id']}"):
                st.session_state.editando_conta_banco_id = c['id']
                st.rerun()
            if col_c[3].button("🗑️", key=f"d_conta_{c['id']}"):
                conn = None
                try:
                    conn = get_connection()
                    cursor = conn.cursor()
                    cursor.execute("DELETE FROM empresa_banco_contas WHERE id = %s", (int(c['id']),))
                    conn.commit()
                    st.toast("Conta deletada!")
                except mysql.connector.Error as err: st.error(f"Erro: {err}")
                finally:
                    if conn: conn.close()
                st.rerun()

    st.subheader("Adicionar / Editar Conta por Banco")
    with st.form("form_conta_banco"):
        current_nome_banco     = ""
        current_conta_contabil = ""
        if st.session_state.editando_conta_banco_id and not df_contas_banco.empty:
            linha_conta = df_contas_banco[df_contas_banco['id'] == st.session_state.editando_conta_banco_id]
            if not linha_conta.empty:
                current_nome_banco     = linha_conta.iloc[0]['nome_banco']
                current_conta_contabil = linha_conta.iloc[0]['conta_contabil']

        banco_options = sorted(list(BANCOS_KEYWORDS.keys()))
        banco_idx     = banco_options.index(current_nome_banco) if current_nome_banco in banco_options else 0

        novo_nome_banco     = st.selectbox("Nome do Banco", banco_options, index=banco_idx)
        nova_conta_contabil = st.text_input("Conta Contábil", value=current_conta_contabil)

        col_cb1, col_cb2 = st.columns(2)
        if col_cb1.form_submit_button("Salvar Conta"):
            if novo_nome_banco and nova_conta_contabil:
                conn = None
                try:
                    conn = get_connection()
                    cursor = conn.cursor()
                    if st.session_state.editando_conta_banco_id:
                        cursor.execute(
                            "UPDATE empresa_banco_contas SET nome_banco=%s, conta_contabil=%s WHERE id=%s",
                            (novo_nome_banco, nova_conta_contabil, st.session_state.editando_conta_banco_id)
                        )
                        st.success("Conta atualizada!")
                    else:
                        cursor.execute(
                            "INSERT INTO empresa_banco_contas (id_empresa, nome_banco, conta_contabil) VALUES (%s,%s,%s)",
                            (id_empresa, novo_nome_banco, nova_conta_contabil)
                        )
                        st.success("Conta adicionada!")
                    conn.commit()
                    st.session_state.editando_conta_banco_id = None
                except mysql.connector.Error as err: st.error(f"Erro: {err}")
                finally:
                    if conn: conn.close()
                st.rerun()
            else:
                st.error("Preencha todos os campos.")

        if col_cb2.form_submit_button("Cancelar"):
            st.session_state.editando_conta_banco_id = None
            st.rerun()
