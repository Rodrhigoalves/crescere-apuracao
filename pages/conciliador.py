import streamlit as st
import pandas as pd
import pdfplumber
import mysql.connector
import io
import re
import unicodedata
import uuid
import os
import tempfile
from thefuzz import fuzz
from ofxparse import OfxParser
import logging
import time

# =============================================================================
# CONFIGURAÇÃO DE OCR E PDF (NUVEM VS LOCAL)
# =============================================================================
try:
    from pdf2image import convert_from_bytes
    import pytesseract
    from PIL import Image
    
    caminho_tesseract_windows = r'C:\Program Files\Tesseract-OCR\tesseract.exe'
    if os.path.exists(caminho_tesseract_windows):
        pytesseract.pytesseract.tesseract_cmd = caminho_tesseract_windows
    
    HAS_OCR = True
except ImportError:
    HAS_OCR = False
    logging.warning("Bibliotecas pdf2image ou pytesseract não instaladas. OCR desativado.")

try:
    from fpdf import FPDF
    HAS_FPDF = True
except ImportError:
    HAS_FPDF = False
    logging.warning("Biblioteca fpdf não instalada. Conversor OFX->PDF desativado.")

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
            'SALDO EM', 'SDO FINAL', 'SDO ANTERIOR', 'SDO CT', 'SALDO BLOQUEADO',
            'SALDO APLIC'
        ]
        if any(b in d for b in bloqueios):
            return True
        if d == 'SALDO' or d == 'SDO':
            return True
        if d.startswith('SALDO ') or d.startswith('SDO '):
            return True
    return False

def limpar_cod_historico(cod):
    if not cod or pd.isna(cod) or str(cod).strip() == "" or str(cod).strip().upper() == "NAN":
        return ""
    try:
        return str(int(float(cod)))
    except ValueError:
        return str(cod).strip()

def inicializar_tabela_bancos():
    conn = None
    try:
        conn = get_connection()
        cursor = conn.cursor()
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS bancos_customizados (
                id INT AUTO_INCREMENT PRIMARY KEY,
                nome VARCHAR(100) NOT NULL UNIQUE
            )
        ''')
        conn.commit()
    except:
        pass
    finally:
        if conn: conn.close()

@st.cache_data(ttl=60, show_spinner=False)
def carregar_bancos_adicionais():
    conn = None
    try:
        conn = get_connection()
        df = pd.read_sql("SELECT nome FROM bancos_customizados", conn)
        return df['nome'].tolist()
    except:
        return []
    finally:
        if conn: conn.close()

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

    if 'linhas_ignoradas_regras' not in st.session_state:
        st.session_state.linhas_ignoradas_regras = []

    for idx, row in df_bruto.iterrows():
        if idx in st.session_state.linhas_ignoradas_regras:
            continue
            
        # Pula as ressalvas (*) para não serem processadas nas regras até a decisão do operador
        if row['Sinal'] == '*':
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
                        'idx_original':  str(idx), 
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

    if 'lancamentos_manuais' in st.session_state and st.session_state.lancamentos_manuais:
        prontos.extend(st.session_state.lancamentos_manuais)

    st.session_state.prontos                 = prontos
    st.session_state.pendentes               = pd.DataFrame(pendentes)
    
    todas_ignoradas = list(set(st.session_state.linhas_ignoradas_regras + linhas_ignoradas_regras))
    st.session_state.linhas_ignoradas_regras = todas_ignoradas

# =============================================================================
# DEFESAS CONTRA EXCEL BANCÁRIO BLINDADAS
# =============================================================================
def converter_data_excel(data_raw):
    data_raw = str(data_raw).strip()
    if data_raw.upper() == 'NAN' or data_raw == '': 
        return None
        
    if " " in data_raw:
        data_raw = data_raw.split(" ")[0]
        
    match = re.search(r'^(\d{2})/(\d{2})/(\d{2,4})', data_raw)
    if match:
        ano = match.group(3)
        if len(ano) == 2: ano = '20' + ano
        return f"{match.group(1)}/{match.group(2)}/{ano}"
    
    match = re.search(r'^(\d{4})-(\d{2})-(\d{2})', data_raw)
    if match:
        return f"{match.group(3)}/{match.group(2)}/{match.group(1)}"
        
    try:
        num = float(data_raw)
        if 30000 < num < 60000:
            dt = pd.to_datetime('1899-12-30') + pd.to_timedelta(num, unit='D')
            return dt.strftime('%d/%m/%Y')
    except Exception:
        pass
        
    return None

def ler_planilha_robusto(file_bytes, nome_arquivo):
    nome_min = nome_arquivo.lower()
    
    if nome_min.endswith(('.xlsx', '.xls')):
        try: 
            df = pd.read_excel(io.BytesIO(file_bytes), header=None, dtype=str)
            if not df.empty and df.shape[1] > 1: return df
        except Exception:
            pass

    for enc in ['utf-8', 'latin1']:
        try:
            dfs = pd.read_html(io.BytesIO(file_bytes), encoding=enc, decimal=',', thousands='.')
            if dfs: 
                df_concat = pd.concat(dfs, ignore_index=True).astype(str)
                if not df_concat.empty and df_concat.shape[1] > 1: return df_concat
        except:
            pass
            
    for sep in [';', ',', '\t']:
        for enc in ['utf-8', 'latin1', 'cp1252']:
            try:
                df = pd.read_csv(io.BytesIO(file_bytes), sep=sep, header=None, dtype=str, encoding=enc)
                if not df.empty and df.shape[1] > 1:
                    return df
            except:
                pass
                
    return pd.DataFrame()

# =============================================================================
# LEITOR UNIVERSAL POWER QUERY (Lê formatos de 3 colunas limpas automaticamente)
# =============================================================================
def extrair_modelo_power_query(df_full):
    if df_full.empty or df_full.shape[1] < 3:
        return pd.DataFrame()
        
    dados = []
    for idx, row in df_full.iterrows():
        data_raw = str(row.iloc[0]).strip()
        desc_raw = str(row.iloc[1]).strip()
        val_raw = str(row.iloc[2]).strip()
        
        data_val = converter_data_excel(data_raw)
        if not data_val:
            continue
            
        desc_limpa = padronizar_texto(desc_raw)
        if eh_linha_de_saldo(desc_limpa) or not desc_limpa or desc_limpa == 'NAN':
            continue
            
        is_asterisk = '*' in val_raw
        
        v_clean = re.sub(r'[^\d.,-]', '', val_raw.replace('*', ''))
        if not v_clean:
            continue
            
        is_negative = '-' in v_clean
        v_clean = v_clean.replace('-', '')
        
        if ',' in v_clean and '.' in v_clean:
            v_clean = v_clean.replace('.', '').replace(',', '.')
        elif ',' in v_clean:
            v_clean = v_clean.replace(',', '.')
            
        try:
            valor_final = float(v_clean)
        except ValueError:
            continue
            
        if valor_final == 0:
            continue
            
        if is_asterisk:
            sinal_final = '*' # Vai para a Quarentena (Triage)
        else:
            sinal_final = '-' if is_negative else '+'
            
        dados.append({
            'Data': data_val,
            'Descricao': desc_limpa,
            'Valor': abs(valor_final),
            'Sinal': sinal_final
        })
        
    return pd.DataFrame(dados)


# =============================================================================
# 3. INTELIGÊNCIA: AUTO-LEITURA E EXTRAÇÃO PDF/OFX
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

@st.cache_data(show_spinner=False)
def motor_conversor_pdf_para_ofx(file_bytes, banco_nome):
    dados = []
    ign = {"criticas": [], "comuns": []}
    
    try:
        with pdfplumber.open(io.BytesIO(file_bytes)) as pdf:
            texto_teste = pdf.pages[0].extract_text()
            if not texto_teste or len(texto_teste.strip()) < 10:
                return pd.DataFrame(), ign
                    
            ano_atual = str(pd.Timestamp.now().year)
            for page in pdf.pages:
                tabelas = page.extract_tables()
                if not tabelas: continue
                
                for tabela in tabelas:
                    transacao_atual = None
                    for linha in tabela:
                        linha = [str(item).strip() if item else "" for item in linha]
                        if not linha or len(linha) < 2: continue
                        
                        texto_busca = " ".join(linha[:2])
                        match_data = re.search(r'(\d{2}/\d{2}(?:/\d{4})?)', texto_busca)
                        
                        if match_data:
                            if transacao_atual: dados.append(transacao_atual)
                            data_f = match_data.group(1)
                            if len(data_f) == 5: data_f += f"/{ano_atual}"
                            
                            valor_str = linha[-1] if linha[-1] else (linha[-2] if len(linha)>2 else "")
                            valor_limpo = re.sub(r'[^\d.,]', '', valor_str)
                            
                            try:
                                val = float(valor_limpo.replace('.', '').replace(',', '.'))
                                sinal = '-' if 'D' in valor_str.upper() or '-' in valor_str else '+'
                            except:
                                val = 0.0
                                sinal = '+'
                            
                            desc = padronizar_texto(linha[1])
                            transacao_atual = {
                                'Data': data_f, 'Descricao': desc, 'Valor': abs(val), 'Sinal': sinal
                            }
                        elif transacao_atual and len(linha) > 1 and linha[1]:
                            texto_extra = padronizar_texto(linha[1])
                            if texto_extra: transacao_atual['Descricao'] += f" {texto_extra}"
                                
                    if transacao_atual: dados.append(transacao_atual)
    except Exception as e:
        logging.warning(f"Motor conversor universal encontrou problema: {e}")
        
    if len(dados) < 2: return pd.DataFrame(), ign
    return pd.DataFrame(dados), ign

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
            if name_pad and name_pad not in VALORES_GENERICOS: partes.append(name_pad)
            if memo_pad and memo_pad not in VALORES_GENERICOS and memo_pad != name_pad:
                if name_pad not in memo_pad and memo_pad not in name_pad: partes.append(memo_pad)
                elif len(memo_pad) > len(name_pad): partes = [memo_pad]

            if not partes: partes.append(trntype.upper() if trntype else 'SEM DESCRICAO')

            descricao_final = " | ".join(partes) if partes else "SEM DESCRICAO"

            if eh_linha_de_saldo(descricao_final): continue

            dados_extraidos.append({
                'Data': data_fmt, 'Descricao': descricao_final, 'Valor': abs(valor), 'Sinal': '+' if valor > 0 else '-'
            })
    except Exception as e:
        logging.exception("Erro na extração OFX")
    return pd.DataFrame(dados_extraidos)

# ==========================================
# LEITORES DE EXCEL (Integrados com Power Query)
# ==========================================
@st.cache_data(show_spinner=False)
def extrair_planilha_bb(file_bytes, nome_arquivo):
    try:
        df_full = ler_planilha_robusto(file_bytes, nome_arquivo)
        if df_full.empty: return pd.DataFrame()
        
        df_pq = extrair_modelo_power_query(df_full)
        if not df_pq.empty:
            return df_pq
        
        header_idx = -1
        for idx, row in df_full.iterrows():
            row_vals = [str(x).upper() for x in row.values if pd.notna(x) and str(x).strip().upper() not in ['NAN', '']]
            row_str = " ".join(row_vals)
            if 'DATA' in row_str and ('HIST' in row_str or 'DESCR' in row_str) and ('VALOR' in row_str or 'R$' in row_str):
                header_idx = idx
                break
        
        if header_idx == -1: return pd.DataFrame()

        df_raw = df_full.iloc[header_idx+1:].copy()
        colunas_limpas = [padronizar_texto(str(c)) for c in df_full.iloc[header_idx].values]
        
        colunas_unicas = []
        for i, col in enumerate(colunas_limpas):
            base = col if col and col != 'NAN' else f"COL_{i}"
            if base in colunas_unicas: base = f"{base}_{i}"
            colunas_unicas.append(base)
        df_raw.columns = colunas_unicas
        
        col_data = next((c for c in colunas_unicas if 'DATA' in c), None)
        col_hist = next((c for c in colunas_unicas if 'HIST' in c and 'COD' not in c), None)
        if not col_hist: col_hist = next((c for c in colunas_unicas if 'DESCR' in c or 'LANC' in c), None)
        
        col_valor = next((c for c in colunas_unicas if 'VALOR' in c), None)
        col_inf   = next((c for c in colunas_unicas if 'INF' in c), None) 
        col_det   = next((c for c in colunas_unicas if 'DETALH' in c or 'COMPL' in c), None)

        if not col_data or not col_valor: return pd.DataFrame()

        dados = []
        for _, row in df_raw.iterrows():
            data_raw = converter_data_excel(str(row[col_data]))
            if not data_raw: continue
            
            desc_parts = []
            if col_hist and pd.notna(row[col_hist]): desc_parts.append(str(row[col_hist]).strip())
            if col_det and pd.notna(row[col_det]): desc_parts.append(str(row[col_det]).strip())
            
            desc_limpa = padronizar_texto(" ".join(desc_parts))
            if not desc_limpa or desc_limpa == 'NAN': desc_limpa = "SEM DESCRICAO"
            if eh_linha_de_saldo(desc_limpa): continue
            
            val_raw = str(row[col_valor]).strip()
            if pd.isna(row[col_valor]) or val_raw.upper() in ['NAN', '']: continue
            
            val_clean = val_raw.replace('*', '').replace(' ', '').strip()
            val_num_str = re.sub(r'[^\d.,]', '', val_clean)
            if not val_num_str: continue
            
            try:
                if ',' in val_num_str and '.' in val_num_str: val_num_str = val_num_str.replace('.', '').replace(',', '.')
                elif ',' in val_num_str: val_num_str = val_num_str.replace(',', '.')
                valor_num = float(val_num_str)
            except ValueError: continue
            
            if valor_num == 0: continue
            
            sinal = '+'
            if col_inf and pd.notna(row[col_inf]):
                inf_val = str(row[col_inf]).upper().strip()
                if inf_val == 'D': sinal = '-'
                elif inf_val == 'C': sinal = '+'
                elif inf_val == '*': sinal = '*' # Vai para a Quarentena
            
            dados.append({'Data': data_raw, 'Descricao': desc_limpa, 'Valor': abs(valor_num), 'Sinal': sinal})
            
        return pd.DataFrame(dados)
    except Exception as e:
        logging.exception(f"Erro BB Excel: {e}")
        return pd.DataFrame()

@st.cache_data(show_spinner=False)
def extrair_planilha_bradesco(file_bytes, nome_arquivo):
    try:
        df_full = ler_planilha_robusto(file_bytes, nome_arquivo)
        if df_full.empty: return pd.DataFrame()
        
        df_pq = extrair_modelo_power_query(df_full)
        if not df_pq.empty:
            return df_pq
            
        header_idx = -1
        for idx, row in df_full.iterrows():
            row_vals = [str(x).upper() for x in row.values if pd.notna(x) and str(x).strip().upper() not in ['NAN', '']]
            row_str = " ".join(row_vals)
            
            if 'DATA' in row_str and ('LANC' in row_str or 'HIST' in row_str):
                if 'CRED' in row_str or 'DEB' in row_str:
                    header_idx = idx
                    break
        
        if header_idx == -1: return pd.DataFrame()
        
        df_raw = df_full.iloc[header_idx+1:].copy()
        colunas_limpas = [padronizar_texto(str(c)) for c in df_full.iloc[header_idx].values]
        
        colunas_unicas = []
        for i, col in enumerate(colunas_limpas):
            base = col if col and col != 'NAN' else f"COL_{i}"
            if base in colunas_unicas: base = f"{base}_{i}"
            colunas_unicas.append(base)
        df_raw.columns = colunas_unicas
        
        col_data = next((c for c in colunas_unicas if 'DATA' in c), None)
        col_lanc = next((c for c in colunas_unicas if 'LANC' in c or 'HIST' in c or 'DESCR' in c), None)
        col_cred = next((c for c in colunas_unicas if 'CRED' in c), None)
        col_deb  = next((c for c in colunas_unicas if 'DEB' in c), None)
        
        if not col_data: return pd.DataFrame()
        
        dados = []
        for _, row in df_raw.iterrows():
            check_str = str(row[col_data]).upper() + " " + str(row[col_lanc]).upper() if col_lanc else ""
            if 'TOTAL' in check_str: break
            
            data_val = converter_data_excel(str(row[col_data]))
            if not data_val: continue
            
            desc_raw = str(row[col_lanc]).strip() if col_lanc and pd.notna(row[col_lanc]) else ""
            desc_limpa = padronizar_texto(desc_raw)
            
            if eh_linha_de_saldo(desc_limpa) or not desc_limpa or desc_limpa == 'NAN': continue
            
            valor_final = 0.0
            sinal_final = '+'
            tem_valor = False
            
            if col_cred and pd.notna(row[col_cred]):
                v_cred = str(row[col_cred]).strip()
                if v_cred and v_cred.upper() != 'NAN' and v_cred != '0':
                    v_clean = re.sub(r'[^\d.,]', '', v_cred)
                    if v_clean:
                        try:
                            if ',' in v_clean and '.' in v_clean: v_clean = v_clean.replace('.', '').replace(',', '.')
                            elif ',' in v_clean: v_clean = v_clean.replace(',', '.')
                            valor_final = float(v_clean)
                            sinal_final = '+'
                            tem_valor = True
                        except ValueError: pass
            
            if not tem_valor and col_deb and pd.notna(row[col_deb]):
                v_deb = str(row[col_deb]).strip()
                if v_deb and v_deb.upper() != 'NAN' and v_deb != '0':
                    v_clean = re.sub(r'[^\d.,]', '', v_deb)
                    if v_clean:
                        try:
                            if ',' in v_clean and '.' in v_clean: v_clean = v_clean.replace('.', '').replace(',', '.')
                            elif ',' in v_clean: v_clean = v_clean.replace(',', '.')
                            valor_final = float(v_clean)
                            sinal_final = '-'
                            tem_valor = True
                        except ValueError: pass
            
            if tem_valor and valor_final > 0:
                sinal = '*' if '*' in desc_raw else sinal_final
                dados.append({
                    'Data': data_val, 'Descricao': desc_limpa, 'Valor': abs(valor_final), 'Sinal': sinal
                })
                
        return pd.DataFrame(dados)
    except Exception as e:
        logging.exception(f"Erro Bradesco Excel: {e}")
        return pd.DataFrame()


# =============================================================================
# GERADOR DE PDF A PARTIR DO OFX
# =============================================================================
def gerar_pdf_do_ofx(file_bytes, nome_arquivo):
    try:
        ofx_text = file_bytes.decode('utf-8')
    except UnicodeDecodeError:
        ofx_text = file_bytes.decode('latin-1', errors='ignore')
        
    ofx = OfxParser.parse(io.BytesIO(ofx_text.encode('utf-8')))
    pdf = FPDF()
    pdf.add_page()
    
    def safe_text(txt):
        if not txt: return ""
        return unicodedata.normalize('NFKD', str(txt)).encode('ASCII', 'ignore').decode('ASCII')
        
    pdf.set_font("Arial", 'B', 14)
    banco_nome = safe_text(ofx.account.institution.organization) if ofx.account.institution else "BANCO DESCONHECIDO"
    conta_id = safe_text(ofx.account.account_id) if ofx.account else "N/A"
    
    pdf.cell(0, 10, f"EXTRATO BANCARIO FORMATADO - {banco_nome}", ln=True, align='C')
    pdf.set_font("Arial", '', 10)
    pdf.cell(0, 8, f"Conta: {conta_id} | Arquivo Original: {safe_text(nome_arquivo)}", ln=True, align='C')
    pdf.ln(5)
    
    pdf.set_font("Arial", 'B', 9)
    pdf.cell(25, 8, "DATA", border=1, align='C')
    pdf.cell(130, 8, "DESCRICAO", border=1)
    pdf.cell(35, 8, "VALOR (R$)", border=1, ln=True, align='C')
    
    pdf.set_font("Arial", '', 8)
    
    txs = []
    if hasattr(ofx.account, 'statement') and ofx.account.statement:
        txs = ofx.account.statement.transactions
        
    for tx in txs:
        data_str = tx.date.strftime("%d/%m/%Y") if tx.date else ""
        desc = safe_text(tx.memo if tx.memo else tx.payee)[:85]
        valor = float(tx.amount)
        
        valor_str = f"{abs(valor):,.2f}".replace(',', 'X').replace('.', ',').replace('X', '.')
        if valor > 0: valor_str = "+ " + valor_str
        else: valor_str = "- " + valor_str
            
        pdf.cell(25, 6, data_str, border=1, align='C')
        pdf.cell(130, 6, desc, border=1)
        pdf.cell(35, 6, valor_str, border=1, ln=True, align='R')
        
    with tempfile.NamedTemporaryFile(delete=False, suffix=".pdf") as tmp:
        pdf.output(tmp.name)
        tmp.seek(0)
        pdf_bytes = tmp.read()
        
    os.unlink(tmp.name)
    return pdf_bytes

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

inicializar_tabela_bancos()

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

df_empresas = carregar_empresas()
if df_empresas.empty:
    st.error("Nenhuma empresa ativa encontrada no banco de dados.")
    st.stop()

# CRIANDO O SISTEMA DE ABAS
tab_conciliacao, tab_conversor = st.tabs(["🏦 Conciliação Bancária", "🧰 Conversor OFX -> PDF"])

with tab_conversor:
    st.markdown("### 🧰 Conversor de Leitura: OFX para PDF")
    st.info("Utilize esta ferramenta para transformar arquivos OFX brutos em documentos PDF formatados.")
    
    if HAS_FPDF:
        ofx_upload = st.file_uploader("Selecione o arquivo .OFX", type=["ofx"], key="uploader_ofx_pdf")
        if ofx_upload:
            if st.button("Gerar PDF Formatado", type="primary"):
                with st.spinner("Lendo OFX e desenhando PDF..."):
                    try:
                        pdf_gerado_bytes = gerar_pdf_do_ofx(ofx_upload.getvalue(), ofx_upload.name)
                        st.success("Conversão concluída com sucesso!")
                        st.download_button(
                            label="📥 BAIXAR EXTRATO PDF",
                            data=pdf_gerado_bytes,
                            file_name=f"{ofx_upload.name.split('.')[0]}_Formatado.pdf",
                            mime="application/pdf"
                        )
                    except Exception as e:
                        st.error(f"Erro ao converter o arquivo. Detalhe: {e}")
    else:
        st.error("⚠️ A biblioteca 'fpdf' não está instalada no servidor. `pip install fpdf`")


with tab_conciliacao:
    st.title("🏦 Conciliação Bancária")

    # =============================================================================
    # PASSO 1 E 2: UPLOAD E PRÉ-SELEÇÃO
    # =============================================================================
    uploaded_files  = st.file_uploader("1. Arraste seus extratos (Excel via Power Query, PDF, OFX)", type=["pdf", "ofx", "xls", "xlsx", "csv"], accept_multiple_files=True)
    
    forcar_universal = st.checkbox("🔄 Forçar Conversão Universal (OFX) em todos os arquivos lidos", value=False, help="Ligue isso caso o arquivo esteja com um layout estranho.")
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

    bancos_nativos = list(BANCOS_KEYWORDS.keys())
    bancos_custom = carregar_bancos_adicionais()
    bancos_disponiveis = sorted(list(set(bancos_nativos + bancos_custom + [st.session_state.banco_detectado])))
    bancos_disponiveis = [b for b in bancos_disponiveis if b != "DESCONHECIDO"]
    
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
            st.session_state.lancamentos_manuais = [] 
            st.session_state.linhas_ignoradas_regras = [] 
            
            with st.spinner("Lendo e classificando extratos..."):
                lista_dfs, criticas, comuns = [], [], []
                houve_falha_pdf = False
                
                for file in uploaded_files:
                    extensao = file.name.lower()
                    
                    if forcar_universal:
                        banco_alvo = banco_selecionado if banco_selecionado != "DESCONHECIDO" else "DESCONHECIDO"
                        df_ex, ign = motor_conversor_pdf_para_ofx(file.getvalue(), banco_alvo)
                        if not df_ex.empty: lista_dfs.append(df_ex)
                        else: st.warning(f"O Extrator Universal não encontrou nada no arquivo: {file.name}")
                            
                    else:
                        if extensao.endswith('.pdf'):
                            banco_pdf = identificar_banco_no_pdf(file.getvalue())
                            banco_alvo = banco_selecionado if banco_selecionado != "DESCONHECIDO" else banco_pdf
                            df_ex, ign = motor_conversor_pdf_para_ofx(file.getvalue(), banco_alvo)
                            
                            if not df_ex.empty:
                                lista_dfs.append(df_ex)
                                criticas.extend(ign['criticas'])
                                comuns.extend(ign['comuns'])
                            else:
                                houve_falha_pdf = True
                                st.error(f"⚠️ O sistema não conseguiu extrair transações do arquivo: {file.name}")
                                
                        elif extensao.endswith(('.xlsx', '.xls', '.csv')):
                            if banco_selecionado == 'BRADESCO':
                                df_ex = extrair_planilha_bradesco(file.getvalue(), file.name)
                            else:
                                df_ex = extrair_planilha_bb(file.getvalue(), file.name)
                                
                            if not df_ex.empty: lista_dfs.append(df_ex)
                            else: st.warning(f"⚠️ O Caçador não encontrou a tabela no arquivo: {file.name}")
                                
                        elif extensao.endswith('.ofx'):
                            df_ex = extrair_texto_ofx(file.getvalue())
                            if not df_ex.empty: lista_dfs.append(df_ex)
                            else: st.warning(f"⚠️ Extrator OFX não encontrou transações em: {file.name}")

                if lista_dfs:
                    df_consolidado = pd.concat(lista_dfs, ignore_index=True)
                    df_consolidado['Valor'] = pd.to_numeric(df_consolidado['Valor'], errors='coerce').fillna(0.0)
                    df_consolidado = df_consolidado[~df_consolidado['Descricao'].apply(eh_linha_de_saldo)].reset_index(drop=True)
                    st.session_state.df_bruto = df_consolidado
                else:
                    st.session_state.df_bruto = pd.DataFrame()
                    
                st.session_state.skipped_indices = []
                st.session_state.criticas        = criticas
                st.session_state.comuns          = comuns
                st.session_state.busca_fila      = ''
                undo_manager.clear()

                if not st.session_state.df_bruto.empty:
                    aplicar_regras_aos_extratos(st.session_state.df_bruto, id_empresa, banco_selecionado, conta_banco_fixa)
                    st.success("Processamento concluído com sucesso!")
                    time.sleep(1)
                    st.rerun()
                elif houve_falha_pdf:
                    st.stop()
                    
    elif conta_banco_fixa == 'N/A':
        st.error("Configure a conta contábil antes de processar.")

    # =============================================================================
    # PASSO 5: RESULTADOS + DETETIVE + AUDITORIA + INCLUSÕES/EXCLUSÕES
    # =============================================================================
    if not st.session_state.df_bruto.empty:
        st.divider()

        # ==========================================
        # MÓDULO DE TRIAGE (QUARENTENA DE RESSALVAS)
        # ==========================================
        df_excecoes = st.session_state.df_bruto[st.session_state.df_bruto['Sinal'] == '*']
        
        if not df_excecoes.empty:
            st.error(f"⚠️ Ação Requerida: O robô identificou **{len(df_excecoes)} lançamento(s)** com ressalvas (*) no extrato. Por favor, defina o destino de cada um antes de prosseguir com a conciliação.")
            
            with st.expander("🔍 Analisar Lançamentos Pendentes (*)", expanded=True):
                with st.form("form_ressalvas"):
                    st.markdown("O sistema encontrou lançamentos bloqueados ou em análise pelo banco e não soube classificar o saldo. Marque abaixo se deseja excluir a linha (mais comum), forçar como Entrada (+) ou Saída (-).")
                    
                    decisoes = {}
                    for idx, row in df_excecoes.iterrows():
                        st.markdown(f"**Data:** {row['Data']} | **Descrição:** {row['Descricao']} | **Valor:** R$ {row['Valor']:.2f}")
                        decisoes[idx] = st.radio(
                            "Decisão para este lançamento:",
                            options=["🗑️ Excluir (Ignorar)", "🟢 Considerar como Entrada (+)", "🔴 Considerar como Saída (-)"],
                            key=f"decisao_{idx}",
                            horizontal=True
                        )
                        st.write("---")
                    
                    if st.form_submit_button("✅ Confirmar Todas as Decisões", type="primary"):
                        df_temp = st.session_state.df_bruto.copy()
                        for idx, decisao in decisoes.items():
                            if "Excluir" in decisao:
                                st.session_state.linhas_ignoradas_regras.append(idx)
                                df_temp.at[idx, 'Sinal'] = 'IGNORADO' # Tira da lista de asteriscos
                            elif "Entrada" in decisao:
                                df_temp.at[idx, 'Sinal'] = '+'
                            elif "Saída" in decisao:
                                df_temp.at[idx, 'Sinal'] = '-'
                        
                        st.session_state.df_bruto = df_temp
                        aplicar_regras_aos_extratos(st.session_state.df_bruto, id_empresa, banco_selecionado, conta_banco_fixa)
                        st.success("Ressalvas atualizadas com sucesso!")
                        time.sleep(1)
                        st.rerun()
            
            # Trava a execução da página aqui para forçar a resolução
            st.stop()

        # ==========================================
        # CÁLCULOS E PAINEL DETETIVE
        # ==========================================
        df_validos = st.session_state.df_bruto[
            (~st.session_state.df_bruto.index.isin(st.session_state.linhas_ignoradas_regras)) &
            (st.session_state.df_bruto['Sinal'] != '*')
        ]
        
        total_e               = float(df_validos[df_validos['Sinal'] == '+']['Valor'].sum())
        total_s               = float(df_validos[df_validos['Sinal'] == '-']['Valor'].sum())
        
        if 'lancamentos_manuais' in st.session_state and st.session_state.lancamentos_manuais:
            for m_item in st.session_state.lancamentos_manuais:
                val_m = float(str(m_item['Valor']).replace('.', '').replace(',', '.'))
                if m_item['Debito'] == conta_banco_fixa: total_e += val_m
                else: total_s += val_m

        saldo_final_calculado = saldo_anterior_informado + total_e - total_s

        c1, c2, c3, c4 = st.columns(4)
        c1.metric("Saldo Anterior",           formatar_moeda(saldo_anterior_informado))
        c2.metric("🟢 Entradas Válidas",      formatar_moeda(total_e))
        c3.metric("🔴 Saídas Válidas",        formatar_moeda(total_s))
        c4.metric("⚖️ Saldo Final Calculado", formatar_moeda(saldo_final_calculado))

        # O DETETIVE
        diferenca_existente = False
        if saldo_final_informado != 0.00:
            diferenca = round(abs(saldo_final_calculado - saldo_final_informado), 2)
            if diferenca > 0.01:
                diferenca_existente = True
                st.error(f"⚠️ **Atenção!** Há uma diferença de **{formatar_moeda(diferenca)}** entre o saldo calculado e o que você informou.")
                
                encontrou_pista = False
                suspeitos_bruto = st.session_state.df_bruto[st.session_state.df_bruto['Valor'] == diferenca]
                if not suspeitos_bruto.empty:
                    st.info(f"💡 **PISTA 1:** Encontrei {len(suspeitos_bruto)} lançamento(s) na fila com o valor exato da diferença. Vá em '🗑️ Excluir Lançamento' abaixo para descartá-lo, se for o caso.")
                    encontrou_pista = True
                    
                metade = round(diferenca / 2, 2)
                suspeitos_metade = st.session_state.df_bruto[st.session_state.df_bruto['Valor'] == metade]
                if not suspeitos_metade.empty:
                    st.info(f"💡 **PISTA 2:** Há um lançamento na fila de **{formatar_moeda(metade)}**. Se o sinal dele estiver invertido, ele gera exatamente essa diferença!")
                    encontrou_pista = True

                str_diff_br = f"{diferenca:.2f}".replace('.', ',')
                suspeitos_lixo = [l for l in st.session_state.criticas if str_diff_br in l]
                if suspeitos_lixo:
                    st.info(f"💡 **PISTA 3:** O valor de {formatar_moeda(diferenca)} aparece nas linhas que o extrator de PDF não conseguiu ler direito (Ignorados Brutos). Adicione este valor em '➕ Adicionar Lançamento Manual'!")
                    encontrou_pista = True

                if not encontrou_pista:
                    st.info("💡 **PISTA:** Não encontrei um culpado exato com esse valor. Essa diferença deve ser a soma de múltiplos lançamentos que faltaram ou vieram a mais.")
            else:
                st.success("✅ **O Saldo Final Calculado bateu perfeitamente com o Saldo Final Informado!**")

        # ==========================================
        # RESUMO DIÁRIO (BATER SALDO POR DIA)
        # ==========================================
        with st.expander("📅 Movimentação Dia a Dia (Bater com o Banco)", expanded=diferenca_existente):
            st.caption("Compare a coluna 'Saldo Final do Dia' com o seu extrato bancário para achar exatamente onde a diferença começou.")
            
            df_daily = df_validos[['Data', 'Valor', 'Sinal']].copy()
            
            manuais_list = []
            if 'lancamentos_manuais' in st.session_state and st.session_state.lancamentos_manuais:
                for m in st.session_state.lancamentos_manuais:
                    val_m = float(str(m['Valor']).replace('.', '').replace(',', '.'))
                    sinal_m = '+' if m['Debito'] == conta_banco_fixa else '-'
                    manuais_list.append({'Data': m['Data'], 'Valor': val_m, 'Sinal': sinal_m})
                    
            if manuais_list:
                df_daily = pd.concat([df_daily, pd.DataFrame(manuais_list)], ignore_index=True)
                
            if not df_daily.empty:
                df_daily['Data_dt'] = pd.to_datetime(df_daily['Data'], format='%d/%m/%Y', errors='coerce')
                df_daily = df_daily.dropna(subset=['Data_dt'])
                
                df_daily['Entradas'] = df_daily.apply(lambda x: x['Valor'] if x['Sinal'] == '+' else 0.0, axis=1)
                df_daily['Saidas'] = df_daily.apply(lambda x: x['Valor'] if x['Sinal'] == '-' else 0.0, axis=1)
                
                resumo_diario = df_daily.groupby('Data_dt').agg({'Entradas': 'sum', 'Saidas': 'sum'}).reset_index()
                resumo_diario = resumo_diario.sort_values('Data_dt')
                
                resumo_diario['Movimentação Líquida'] = resumo_diario['Entradas'] - resumo_diario['Saidas']
                resumo_diario['Saldo Final do Dia'] = saldo_anterior_informado + resumo_diario['Movimentação Líquida'].cumsum()
                
                resumo_diario['Data'] = resumo_diario['Data_dt'].dt.strftime('%d/%m/%Y')
                resumo_fmt = resumo_diario[['Data', 'Entradas', 'Saidas', 'Movimentação Líquida', 'Saldo Final do Dia']].copy()
                
                st.dataframe(
                    resumo_fmt.style.format({
                        "Entradas": "R$ {:,.2f}",
                        "Saidas": "R$ {:,.2f}",
                        "Movimentação Líquida": "R$ {:,.2f}",
                        "Saldo Final do Dia": "R$ {:,.2f}"
                    }),
                    use_container_width=True,
                    hide_index=True
                )
            else:
                st.info("Nenhuma movimentação processada para calcular o saldo diário.")

        # ==========================================
        # PAINÉIS DE AJUSTE DE SALDO (INCLUIR / EXCLUIR)
        # ==========================================
        col_ajuste1, col_ajuste2 = st.columns(2)

        with col_ajuste1.expander("➕ Adicionar Lançamento Manual (Ajuste de Saldo)", expanded=False):
            st.caption("Faltou alguma coisa? Insira aqui e o saldo recalcula na hora.")
            m_data = st.text_input("Data (DD/MM/AAAA)")
            m_desc = st.text_input("Descrição do Lançamento")
            m_valor = st.number_input("Valor (R$)", step=0.01, format="%.2f")
            m_sinal = st.selectbox("Tipo", ["- (Saída)", "+ (Entrada)"])
            m_conta = st.text_input("Conta Contrapartida")
            
            if st.button("Adicionar Lançamento Manual", type="primary"):
                if m_data and m_desc and m_valor > 0 and m_conta:
                    sinal_final = '+' if '+' in m_sinal else '-'
                    novo_item = {
                        'idx_original':  f"manual_{uuid.uuid4().hex}",
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
                    
                    aplicar_regras_aos_extratos(st.session_state.df_bruto, id_empresa, banco_selecionado, conta_banco_fixa)
                    st.toast("Lançamento inserido e saldo atualizado!")
                    st.rerun()
                else:
                    st.error("Preencha todos os campos corretamente.")

        with col_ajuste2.expander("🗑️ Excluir Lançamento (Ajuste de Saldo)", expanded=False):
            st.caption("Selecione lançamentos do banco (ou inseridos manualmente) que devem ser **ignorados na conta matemática e no ERP**.")
            
            opcoes_exclusao = []
            if not df_validos.empty:
                for idx, row in df_validos.iterrows():
                    tipo_str = "Entrada" if row['Sinal'] == '+' else "Saída"
                    opcoes_exclusao.append(f"[{idx}] {row['Data']} - {tipo_str} - R$ {row['Valor']:.2f} - {row['Descricao']}")
                    
            if 'lancamentos_manuais' in st.session_state and st.session_state.lancamentos_manuais:
                for m in st.session_state.lancamentos_manuais:
                    s_m = "+" if m['Debito'] == conta_banco_fixa else "-"
                    tipo_str_m = "Entrada" if s_m == '+' else "Saída"
                    opcoes_exclusao.append(f"[{m['idx_original']}] {m['Data']} - {tipo_str_m} - R$ {m['Valor']} - MANUAL: {m['Historico']}")

            if opcoes_exclusao:
                itens_para_excluir = st.multiselect("Pesquise e selecione os itens:", opcoes_exclusao)
                
                if st.button("❌ Confirmar Exclusão e Recalcular", type="primary"):
                    for item in itens_para_excluir:
                        idx_str = item.split(']')[0][1:] 
                        
                        if str(idx_str).startswith('manual_'):
                            st.session_state.lancamentos_manuais = [m for m in st.session_state.lancamentos_manuais if m['idx_original'] != idx_str]
                        else:
                            st.session_state.linhas_ignoradas_regras.append(int(idx_str))
                    
                    aplicar_regras_aos_extratos(st.session_state.df_bruto, id_empresa, banco_selecionado, conta_banco_fixa)
                    st.toast("Lançamentos excluídos com sucesso! Saldo recalculado.")
                    st.rerun()
            else:
                st.info("Não há lançamentos disponíveis para exclusão.")

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
                            time.sleep(0.5)
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
                        time.sleep(0.5)
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
                df_prontos = pd.DataFrame(st.session_state.prontos)
                
                if 'idx_original' in df_prontos.columns:
                    df_prontos = df_prontos.drop(columns=['idx_original'])
                    
                if 'Debito' in df_prontos.columns:
                    idx_debito = df_prontos.columns.get_loc('Debito')
                    df_prontos.insert(idx_debito, ' ', '')
                
                output = io.BytesIO()
                with pd.ExcelWriter(output, engine='openpyxl') as writer:
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
                        time.sleep(0.5)
                        st.rerun()
                    if st.form_submit_button("Cancelar"):
                        st.session_state.editando_regra_id = None
                        st.rerun()

    with st.expander("➕ Cadastrar Novo Banco Oficial", expanded=False):
        with st.form("form_novo_banco"):
            nome_novo_banco = st.text_input("Nome do Banco (Ex: INTER, SICREDI, BTG)")
            if st.form_submit_button("Adicionar à Lista"):
                nome_formatado = padronizar_texto(nome_novo_banco).strip()
                if nome_formatado:
                    if nome_formatado in bancos_disponiveis: st.warning("Este banco já está na lista oficial.")
                    else:
                        conn = None
                        try:
                            conn = get_connection()
                            cursor = conn.cursor()
                            cursor.execute("INSERT INTO bancos_customizados (nome) VALUES (%s)", (nome_formatado,))
                            conn.commit()
                            st.success(f"Banco '{nome_formatado}' adicionado com sucesso! Atualize a página.")
                        except mysql.connector.Error as err: st.error(f"Erro ao cadastrar banco. Detalhe: {err}")
                        finally:
                            if conn: conn.close()
                else: st.error("Digite um nome válido.")

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

            banco_idx = bancos_disponiveis.index(current_nome_banco) if current_nome_banco in bancos_disponiveis else 0

            novo_nome_banco     = st.selectbox("Nome do Banco", bancos_disponiveis, index=banco_idx)
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
                    time.sleep(0.5)
                    st.rerun()
                else:
                    st.error("Preencha todos os campos.")

            if col_cb2.form_submit_button("Cancelar"):
                st.session_state.editando_conta_banco_id = None
                st.rerun()
