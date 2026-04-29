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
# CONFIGURAÇÃO DE OCR E PDF
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
    logging.warning("OCR desativado.")

try:
    from fpdf import FPDF
    HAS_FPDF = True
except ImportError:
    HAS_FPDF = False
    logging.warning("FPDF desativado.")

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

# =============================================================================
# UNDO STACK
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
# UTILITÁRIOS
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
        logging.error(f"Erro MySQL: {err}")
        st.error(f"Erro ao conectar: {err}")
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

def eh_linha_de_saldo(descricao):
    d = padronizar_texto(descricao)
    if 'SALDO' in d or 'SDO' in d:
        bloqueios = ['SALDO ANTERIOR', 'SALDO FINAL', 'SALDO DO DIA', 'SALDO DIA', 
                     'SALDO EM', 'SDO FINAL', 'SDO ANTERIOR', 'SDO CT', 'SALDO BLOQUEADO', 'SALDO APLIC']
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
        cursor.execute('''CREATE TABLE IF NOT EXISTS bancos_customizados (id INT AUTO_INCREMENT PRIMARY KEY, nome VARCHAR(100) NOT NULL UNIQUE)''')
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
# REGRAS
# =============================================================================
def aplicar_regras_aos_extratos(df_bruto, id_empresa, banco_selecionado, conta_banco_fixa):
    if df_bruto.empty:
        return

    conn = get_connection()
    regras = pd.read_sql(
        """SELECT * FROM tb_extratos_regras WHERE id_empresa = %s AND banco_nome = %s ORDER BY LENGTH(termo_chave) DESC""",
        conn, params=(id_empresa, banco_selecionado)
    )
    conn.close()

    prontos, pendentes, linhas_ignoradas_regras = [], [], []
    if 'linhas_ignoradas_regras' not in st.session_state:
        st.session_state.linhas_ignoradas_regras = []

    for idx, row in df_bruto.iterrows():
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
                    debito_conta = conta_banco_fixa if row['Sinal'] == '+' else r['conta_contabil']
                    credito_conta = r['conta_contabil'] if row['Sinal'] == '+' else conta_banco_fixa
                    prontos.append({
                        'idx_original': str(idx), 'Debito': debito_conta, 'Credito': credito_conta,
                        'Data': row['Data'], 'Valor': f"{row['Valor']:.2f}".replace('.', ','),
                        'Cod_Historico': limpar_cod_historico(r['cod_historico_erp']),
                        'Historico': r['historico_padrao'] if r['historico_padrao'] else row['Descricao']
                    })
                match = True
                break
        if not match:
            pendentes.append({'idx_original': idx, **row})

    if 'lancamentos_manuais' in st.session_state and st.session_state.lancamentos_manuais:
        prontos.extend(st.session_state.lancamentos_manuais)

    st.session_state.prontos = prontos
    st.session_state.pendentes = pd.DataFrame(pendentes)
    todas_ignoradas = list(set(st.session_state.linhas_ignoradas_regras + linhas_ignoradas_regras))
    st.session_state.linhas_ignoradas_regras = todas_ignoradas

# =============================================================================
# LEITURA ROBUSTA
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
    is_xls = nome_min.endswith('.xls')
    
    # Tenta ler com pandas
    try:
        if is_xls:
            # Força xlrd para .xls
            df = pd.read_excel(io.BytesIO(file_bytes), header=None, dtype=str, engine='xlrd')
        else:
            df = pd.read_excel(io.BytesIO(file_bytes), header=None, dtype=str)
        
        if not df.empty and df.shape[1] > 1:
            return df
    except Exception as e:
        logging.warning(f"Erro pd.read_excel: {e}")
        if is_xls:
            st.warning("⚠️ Arquivo .XLS detectado. Certifique-se de que a biblioteca 'xlrd' está instalada: `pip install xlrd`")

    # Fallbacks
    for enc in ['utf-8', 'latin1']:
        try:
            dfs = pd.read_html(io.BytesIO(file_bytes), encoding=enc, decimal=',', thousands='.')
            if dfs: 
                df_concat = pd.concat(dfs, ignore_index=True).astype(str)
                if not df_concat.empty and df_concat.shape[1] > 1: 
                    return df_concat
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
# EXTRAÇÃO BB CORRIGIDA
# =============================================================================
@st.cache_data(show_spinner=False)
def extrair_planilha_bb(file_bytes, nome_arquivo):
    try:
        df_full = ler_planilha_robusto(file_bytes, nome_arquivo)
        if df_full.empty:
            logging.warning("BB: DataFrame vazio após leitura robusta.")
            return pd.DataFrame()
        
        # Detectar cabeçalho
        header_idx = -1
        for idx, row in df_full.iterrows():
            row_vals = [str(x).upper() for x in row.values if pd.notna(x) and str(x).strip().upper() not in ['NAN', '']]
            row_str = " ".join(row_vals)
            # Busca por DATA, HISTORICO e VALOR
            if 'DATA' in row_str and ('HIST' in row_str or 'DESCR' in row_str) and ('VALOR' in row_str or 'R$' in row_str):
                header_idx = idx
                break
        
        if header_idx == -1:
            logging.warning("BB: Cabeçalho não encontrado.")
            return pd.DataFrame()

        # Preparar dados
        df_raw = df_full.iloc[header_idx+1:].copy()
        colunas_limpas = [padronizar_texto(str(c)) for c in df_full.iloc[header_idx].values]
        
        colunas_unicas = []
        for i, col in enumerate(colunas_limpas):
            base = col if col and col != 'NAN' else f"COL_{i}"
            if base in colunas_unicas:
                base = f"{base}_{i}"
            colunas_unicas.append(base)
        df_raw.columns = colunas_unicas
        
        # Mapear colunas
        col_data = next((c for c in colunas_unicas if 'DATA' in c), None)
        col_hist = next((c for c in colunas_unicas if 'HIST' in c and 'COD' not in c), None)
        if not col_hist:
            col_hist = next((c for c in colunas_unicas if 'DESCR' in c or 'LANC' in c), None)
        
        col_valor = next((c for c in colunas_unicas if 'VALOR' in c), None)
        # Detecta coluna Inf. mesmo se tiver ponto ou espaço
        col_inf = next((c for c in colunas_unicas if 'INF' in c), None)
        col_det = next((c for c in colunas_unicas if 'DETALH' in c or 'COMPL' in c), None)

        if not col_data or not col_valor:
            logging.warning(f"BB: Colunas essenciais não encontradas. Data: {col_data}, Valor: {col_valor}")
            return pd.DataFrame()

        dados = []
        for _, row in df_raw.iterrows():
            data_raw = converter_data_excel(str(row[col_data]))
            if not data_raw:
                continue
            
            # Descrição
            desc_parts = []
            if col_hist and pd.notna(row[col_hist]):
                desc_parts.append(str(row[col_hist]).strip())
            if col_det and pd.notna(row[col_det]):
                desc_parts.append(str(row[col_det]).strip())
            
            desc_limpa = padronizar_texto(" ".join(desc_parts))
            if not desc_limpa or desc_limpa == 'NAN':
                desc_limpa = "SEM DESCRICAO"
            
            if eh_linha_de_saldo(desc_limpa):
                continue
            
            # Valor
            val_raw = str(row[col_valor]).strip()
            if pd.isna(row[col_valor]) or val_raw.upper() in ['NAN', '']:
                continue
            
            val_clean = val_raw.replace('*', '').replace(' ', '').strip()
            val_num_str = re.sub(r'[^\d.,]', '', val_clean)
            
            if not val_num_str:
                continue
            
            try:
                if ',' in val_num_str and '.' in val_num_str:
                    val_num_str = val_num_str.replace('.', '').replace(',', '.')
                elif ',' in val_num_str:
                    val_num_str = val_num_str.replace(',', '.')
                valor_num = float(val_num_str)
            except ValueError:
                continue
            
            if valor_num == 0:
                continue
            
            # Sinal: Prioridade na coluna Inf.
            sinal = '+'
            is_ignorado = False
            
            if col_inf and pd.notna(row[col_inf]):
                inf_val = str(row[col_inf]).upper().strip()
                if inf_val == 'D':
                    sinal = '-'
                elif inf_val == 'C':
                    sinal = '+'
                elif inf_val == '*':
                    is_ignorado = True
                else:
                    # Fallback: Se Inf. tiver valor desconhecido, tenta inferir pelo histórico
                    if any(x in desc_limpa for x in ['CREDITO', 'RECEBIDO', 'DEPOSITO', 'TED CREDITO', 'PIX RECEBIDO']):
                        sinal = '+'
                    elif any(x in desc_limpa for x in ['DEBITO', 'PAGAMENTO', 'SAQUE', 'TRANSFERENCIA ENVIADA']):
                        sinal = '-'
            
            if is_ignorado:
                continue
            
            dados.append({
                'Data': data_raw,
                'Descricao': desc_limpa,
                'Valor': abs(valor_num),
                'Sinal': sinal
            })
            
        return pd.DataFrame(dados)
        
    except Exception as e:
        logging.exception(f"Erro BB Excel: {e}")
        return pd.DataFrame()

# =============================================================================
# EXTRAÇÃO BRADESCO CORRIGIDA
# =============================================================================
@st.cache_data(show_spinner=False)
def extrair_planilha_bradesco(file_bytes, nome_arquivo):
    try:
        df_full = ler_planilha_robusto(file_bytes, nome_arquivo)
        if df_full.empty:
            logging.warning("Bradesco: DataFrame vazio.")
            return pd.DataFrame()
        
        # Detectar cabeçalho ignorando metadados
        header_idx = -1
        for idx, row in df_full.iterrows():
            row_vals = [str(x).upper() for x in row.values if pd.notna(x) and str(x).strip().upper() not in ['NAN', '']]
            row_str = " ".join(row_vals)
            
            if 'DATA' in row_str and ('LANC' in row_str or 'HIST' in row_str):
                if 'CRED' in row_str or 'DEB' in row_str:
                    header_idx = idx
                    break
        
        if header_idx == -1:
            logging.warning("Bradesco: Cabeçalho não encontrado.")
            return pd.DataFrame()
        
        df_raw = df_full.iloc[header_idx+1:].copy()
        colunas_limpas = [padronizar_texto(str(c)) for c in df_full.iloc[header_idx].values]
        
        colunas_unicas = []
        for i, col in enumerate(colunas_limpas):
            base = col if col and col != 'NAN' else f"COL_{i}"
            if base in colunas_unicas:
                base = f"{base}_{i}"
            colunas_unicas.append(base)
        df_raw.columns = colunas_unicas
        
        col_data = next((c for c in colunas_unicas if 'DATA' in c), None)
        col_lanc = next((c for c in colunas_unicas if 'LANC' in c or 'HIST' in c or 'DESCR' in c), None)
        col_cred = next((c for c in colunas_unicas if 'CRED' in c), None)
        col_deb  = next((c for c in colunas_unicas if 'DEB' in c), None)
        
        if not col_data:
            return pd.DataFrame()
        
        dados = []
        for _, row in df_raw.iterrows():
            check_str = str(row[col_data]).upper() + " " + str(row[col_lanc]).upper() if col_lanc else ""
            if 'TOTAL' in check_str:
                break
            
            data_val = converter_data_excel(str(row[col_data]))
            if not data_val:
                continue
            
            desc_raw = str(row[col_lanc]).strip() if col_lanc and pd.notna(row[col_lanc]) else ""
            desc_limpa = padronizar_texto(desc_raw)
            
            if eh_linha_de_saldo(desc_limpa) or not desc_limpa or desc_limpa == 'NAN':
                continue
            
            valor_final = 0.0
            sinal_final = '+'
            tem_valor = False
            
            # Tenta Crédito
            if col_cred and pd.notna(row[col_cred]):
                v_cred = str(row[col_cred]).strip()
                if v_cred and v_cred.upper() != 'NAN' and v_cred != '0':
                    v_clean = re.sub(r'[^\d.,]', '', v_cred)
                    if v_clean:
                        try:
                            if ',' in v_clean and '.' in v_clean:
                                v_clean = v_clean.replace('.', '').replace(',', '.')
                            elif ',' in v_clean:
                                v_clean = v_clean.replace(',', '.')
                            valor_final = float(v_clean)
                            sinal_final = '+'
                            tem_valor = True
                        except ValueError:
                            pass
            
            # Tenta Débito
            if not tem_valor and col_deb and pd.notna(row[col_deb]):
                v_deb = str(row[col_deb]).strip()
                if v_deb and v_deb.upper() != 'NAN' and v_deb != '0':
                    v_clean = re.sub(r'[^\d.,-]', '', v_deb) # Mantém sinal para checagem
                    v_clean_num = re.sub(r'[^\d.,]', '', v_deb)
                    if v_clean_num:
                        try:
                            if ',' in v_clean_num and '.' in v_clean_num:
                                v_clean_num = v_clean_num.replace('.', '').replace(',', '.')
                            elif ',' in v_clean_num:
                                v_clean_num = v_clean_num.replace(',', '.')
                            valor_final = float(v_clean_num)
                            sinal_final = '-'
                            tem_valor = True
                        except ValueError:
                            pass
            
            if tem_valor and valor_final > 0:
                dados.append({
                    'Data': data_val,
                    'Descricao': desc_limpa,
                    'Valor': abs(valor_final),
                    'Sinal': sinal_final
                })
                
        return pd.DataFrame(dados)
        
    except Exception as e:
        logging.exception(f"Erro Bradesco Excel: {e}")
        return pd.DataFrame()

# ... [O restante do código (PDF, OFX, Interface, etc.) permanece igual ao anterior, mantendo a estrutura completa] ...
# Por brevidade, estou fornecendo as funções críticas corrigidas. 
# O código completo deve incluir todas as outras funções do seu script original.
# Se precisar do arquivo completo com todas as linhas, posso gerar, mas as correções essenciais estão acima.
