import streamlit as st
import pandas as pd
import io
import re
import time
import unicodedata
from datetime import datetime, timedelta

# --- CONFIGURAÇÃO ---
st.set_page_config(page_title="CONCILIACAO FATURAMENTO vs CARTAO DE CREDITO", page_icon="💰", layout="wide")

try:
    from fpdf import FPDF
    FPDF_INSTALADO = True
except ImportError:
    FPDF_INSTALADO = False

# --- MEMÓRIA DO SISTEMA ---
if "base_consolidada" not in st.session_state:
    st.session_state.base_consolidada = None
if "maquinas_detalhado" not in st.session_state:
    st.session_state.maquinas_detalhado = {}
if "bases_ajustadas" not in st.session_state:
    st.session_state.bases_ajustadas = {}
if "total_vendas_rejeitadas" not in st.session_state:
    st.session_state.total_vendas_rejeitadas = 0.0
if "tempo_proc" not in st.session_state:
    st.session_state.tempo_proc = 0.0
if "t_start_erp" not in st.session_state:
    st.session_state.t_start_erp = None

# --- FUNÇÕES DE APOIO ---
def formatar_br(valor):
    if pd.isna(valor): return "R$ 0,00"
    return f"R$ {valor:,.2f}".replace(',', 'X').replace('.', ',').replace('X', '.')

def limpar_valor(valor):
    if pd.isna(valor) or valor is None:
        return 0.0
    if isinstance(valor, (int, float)):
        return float(valor)
    
    v = str(valor).strip()
    v_upper = v.upper()
    if v_upper in ["", "NAN", "NONE"] or v_upper.endswith('D') or v_upper.endswith('C'):
        return 0.0
    
    v = v.replace('R$', '').replace('R$ ', '').strip()
    
    if ',' in v and '.' not in v:
        v = v.replace(',', '.')
    elif '.' in v and ',' not in v:
        pass
    elif ',' in v and '.' in v:
        last_comma = v.rfind(',')
        last_dot = v.rfind('.')
        if last_comma > last_dot:
            v = v.replace('.', '').replace(',', '.')
        else:
            v = v.replace(',', '')
    
    v = re.sub(r'[^\d.]', '', v)
    parts = v.split('.')
    if len(parts) > 2:
        v = ''.join(parts[:-1]) + '.' + parts[-1]
    
    try:
        return float(v)
    except:
        return 0.0

def normalizar_data(valor):
    if pd.isna(valor) or valor is None: return None
    try:
        dt = pd.to_datetime(valor, dayfirst=True, errors='coerce')
        if pd.notna(dt): return dt.tz_localize(None).normalize()
    except: pass
    return None

def formatar_periodo(lista_meses):
    if not lista_meses or len(lista_meses) == 0: return "Período não definido"
    if len(lista_meses) == 1: return lista_meses[0]
    meses_ordenados = sorted(lista_meses, key=lambda x: datetime.strptime(x, '%m/%Y'))
    datas = [datetime.strptime(m, '%m/%Y') for m in meses_ordenados]
    consecutivos = True
    for i in range(1, len(datas)):
        diff = (datas[i].year - datas[i-1].year) * 12 + (datas[i].month - datas[i-1].month)
        if diff != 1:
            consecutivos = False
            break
    if consecutivos:
        primeiro = datas[0].strftime('%b/%Y').upper()
        ultimo = datas[-1].strftime('%b/%Y').upper()
        return f"{primeiro} a {ultimo}"
    else:
        nomes_meses = [d.strftime('%b/%Y').upper() for d in datas]
        return ", ".join(nomes_meses)

def detectar_coluna_pix(df, nome_operadora):
    nome_op = nome_operadora.upper()
    colunas = df.columns.tolist()
    mapeamento = {
        'CIELO': ['Forma de pagamento', 'FORMA DE PAGAMENTO', 'forma de pagamento', 'Forma_Pagamento', 'FORMA_PAGAMENTO', 'TP', 'TIPO', 'tipo'],
        'PAGBANK': ['Forma de pagamento', 'FORMA DE PAGAMENTO', 'forma de pagamento', 'Bandeira', 'BANDEIRA', 'bandeira', 'BAND', 'band'],
        'REDE': ['Modalidade', 'MODALIDADE', 'modalidade', 'Bandeira', 'BANDEIRA', 'bandeira', 'Forma de pagamento', 'FORMA DE PAGAMENTO'],
        'SIPAG': ['Bandeira', 'BANDEIRA', 'bandeira', 'Forma de pagamento', 'FORMA DE PAGAMENTO', 'forma de pagamento'],
        'STONE': ['Bandeira', 'BANDEIRA', 'bandeira', 'Produto', 'PRODUTO', 'produto', 'Forma de pagamento', 'FORMA DE PAGAMENTO'],
        'CURINGA': ['Forma de pagamento', 'FORMA DE PAGAMENTO', 'forma de pagamento', 'Bandeira', 'BANDEIRA', 'bandeira', 'Modalidade', 'MODALIDADE', 'Tipo', 'TIPO', 'tipo']
    }
    possiveis_colunas = mapeamento.get(nome_op, ['Forma de pagamento', 'FORMA DE PAGAMENTO', 'forma de pagamento', 'Bandeira', 'BANDEIRA', 'Modalidade', 'MODALIDADE'])
    
    for col in colunas:
        col_str = str(col).strip()
        for possivel in possiveis_colunas:
            if col_str == possivel: return col
            
    for col in colunas:
        col_upper = str(col).upper().strip()
        for possivel in possiveis_colunas:
            if possivel.upper() in col_upper: return col
    return None

def extrair_valor_pix(df, nome_operadora):
    col_pix = detectar_coluna_pix(df, nome_operadora)
    if col_pix is None or col_pix not in df.columns:
        total = df['VALOR_OK'].sum() if 'VALOR_OK' in df.columns else 0
        return total, 0, total
    
    mask_pix = df[col_pix].astype(str).str.upper().str.contains('PIX', na=False)
    valor_pix = df.loc[mask_pix, 'VALOR_OK'].sum() if 'VALOR_OK' in df.columns else 0
    valor_total = df['VALOR_OK'].sum() if 'VALOR_OK' in df.columns else 0
    valor_sem_pix = valor_total - valor_pix
    
    return valor_total, valor_pix, valor_sem_pix

def redistribuir_saldos(df_mes):
    df_res = df_mes.copy().sort_values('DATA').reset_index(drop=True)
    
    while (df_res['DIFERENÇA (EM ESPÉCIE)'] < -0.01).any():
        neg_indices = df_res[df_res['DIFERENÇA (EM ESPÉCIE)'] < -0.01].index
        pos_indices = df_res[df_res['DIFERENÇA (EM ESPÉCIE)'] > 0.01].index
        
        if len(pos_indices) == 0:
            break
            
        idx_neg = neg_indices[0]
        data_neg = df_res.loc[idx_neg, 'DATA']
        
        distancias = abs((df_res.loc[pos_indices, 'DATA'] - data_neg).dt.days)
        idx_pos = pos_indices[distancias.argmin()]
        
        valor_precisa = abs(df_res.loc[idx_neg, 'DIFERENÇA (EM ESPÉCIE)'])
        valor_disponivel = df_res.loc[idx_pos, 'DIFERENÇA (EM ESPÉCIE)']
        
        transferencia = min(valor_precisa, valor_disponivel)
        
        df_res.loc[idx_neg, 'TOTAL_CARTOES'] -= transferencia
        df_res.loc[idx_pos, 'TOTAL_CARTOES'] += transferencia
        
        df_res.loc[idx_neg, 'DIFERENÇA (EM ESPÉCIE)'] = df_res.loc[idx_neg, 'LIVRO_RAZAO'] - df_res.loc[idx_neg, 'TOTAL_CARTOES']
        df_res.loc[idx_pos, 'DIFERENÇA (EM ESPÉCIE)'] = df_res.loc[idx_pos, 'LIVRO_RAZAO'] - df_res.loc[idx_pos, 'TOTAL_CARTOES']
        
    return df_res

# --- GERAÇÃO DE PDF PROFISSIONAL TABULAR ---
if FPDF_INSTALADO:
    class RelatorioDRM(FPDF):
        def header(self):
            self.set_font('Arial', 'B', 14)
            self.set_text_color(31, 119, 180)
            self.cell(0, 10, 'CONCILIACAO FATURAMENTO vs CARTAO DE CREDITO', 0, 1, 'C')
            self.ln(5)
        
        def footer(self):
            self.set_y(-15)
            self.set_font('Arial', 'I', 8)
            self.set_text_color(160, 160, 160)
            self.cell(0, 10, 'Desenvolvido por Rodrhigo Alves | Conciliacao e Auditoria Contabil', 0, 0, 'L')
            self.set_x(self.l_margin)
            self.cell(0, 10, f'Pagina {self.page_no()}', 0, 0, 'R')
            self.set_text_color(0, 0, 0)

def gerar_pdf_final(df_f, detalhes_m, detalhes_pix, meses_selecionados, total_livro, nome_empresa, tipo_relatorio="REAL"):
    if not FPDF_INSTALADO: return None
    pdf = RelatorioDRM()
    pdf.add_page()
    periodo_str = formatar_periodo(meses_selecionados) if meses_selecionados else "Todos os Meses"
    data_geracao = (datetime.utcnow() - timedelta(hours=3)).strftime('%d/%m/%Y')
    
    pdf.set_font('Arial', 'B', 12)
    pdf.set_text_color(0, 0, 0)
    
    if nome_empresa:
        pdf.cell(0, 10, nome_empresa.upper(), 0, 1, 'C')
        pdf.ln(2)
        
    if tipo_relatorio == "AJUSTADO":
        pdf.set_font('Arial', 'B', 10)
        pdf.set_text_color(200, 0, 0)
        pdf.cell(0, 6, "*** RELATORIO DE SALDOS REDISTRIBUIDOS ***", 0, 1, 'C')
        pdf.set_text_color(0, 0, 0)
        pdf.ln(2)
    
    pdf.set_font('Arial', '', 10)
    pdf.cell(0, 8, f"Periodo de Analise: {periodo_str}", 0, 1, 'C')
    pdf.cell(0, 6, f"Gerado em: {data_geracao}", 0, 1, 'C')
    pdf.ln(8)
    
    pdf.set_fill_color(31, 119, 180)
    pdf.set_text_color(255, 255, 255)
    pdf.set_font('Arial', 'B', 11)
    pdf.cell(0, 10, " 1. RESUMO GERAL", 0, 1, 'L', fill=True)
    pdf.ln(4)
    
    pdf.set_text_color(0, 0, 0)
    
    pdf.set_font('Arial', 'B', 10)
    pdf.cell(50, 6, "Livro Razao:", 0, 0, 'L')
    pdf.set_font('Arial', '', 10)
    pdf.cell(0, 6, formatar_br(total_livro), 0, 1, 'L')
    
    total_maquinas = sum(d['total'] for d in detalhes_pix.values())
    pdf.set_font('Arial', 'B', 10)
    pdf.cell(50, 6, "Total Maquinas:", 0, 0, 'L')
    pdf.set_font('Arial', '', 10)
    pdf.cell(0, 6, formatar_br(total_maquinas), 0, 1, 'L')
    pdf.ln(4)
    
    pdf.set_font('Arial', 'B', 9)
    pdf.set_fill_color(240, 240, 240)
    
    w_op = [40, 50, 50, 50]
    pdf.cell(w_op[0], 8, "OPERADORA", 1, 0, 'C', fill=True)
    pdf.cell(w_op[1], 8, "VALOR APURADO", 1, 0, 'C', fill=True)
    pdf.cell(w_op[2], 8, "DESPESAS", 1, 0, 'C', fill=True)
    pdf.cell(w_op[3], 8, "PIX", 1, 1, 'C', fill=True)
    
    pdf.set_font('Arial', '', 9)
    tem_dados = False
    
    for nome_op, dados in detalhes_pix.items():
        if dados['total'] > 0:
            tem_dados = True
            valor_exibicao = dados['sem_pix'] if dados['pix'] > 0 else dados['total']
            
            pdf.cell(w_op[0], 7, f" {nome_op}", 1, 0, 'L')
            pdf.cell(w_op[1], 7, formatar_br(valor_exibicao), 1, 0, 'R')
            pdf.cell(w_op[2], 7, formatar_br(dados.get('despesa', 0)), 1, 0, 'R')
            pdf.cell(w_op[3], 7, formatar_br(dados['pix']), 1, 1, 'R')
            
    if not tem_dados:
        pdf.cell(sum(w_op), 7, "Nenhum movimento de cartao no periodo", 1, 1, 'C')
        
    pdf.ln(4)
    
    valor_especie = total_livro - total_maquinas
    pdf.set_font('Arial', 'B', 10)
    pdf.set_text_color(200, 0, 0)
    pdf.cell(50, 8, "Diferenca (Em Especie):", 0, 0, 'L')
    pdf.cell(0, 8, formatar_br(valor_especie), 0, 1, 'L')
    pdf.set_text_color(0, 0, 0)
    
    pdf.ln(5)
    
    pdf.set_fill_color(31, 119, 180)
    pdf.set_text_color(255, 255, 255)
    pdf.set_font('Arial', 'B', 11)
    pdf.cell(0, 10, " 2. DETALHAMENTO DIARIO", 0, 1, 'L', fill=True)
    pdf.ln(2)
    
    pdf.set_text_color(0, 0, 0)
    pdf.set_font('Arial', 'B', 8)
    
    ls = [30, 53, 53, 54]
    hs = ['DATA', 'VALOR RAZAO', 'TOTAL CARTOES', 'VALOR EM ESPECIE']
    for i, c in enumerate(hs):
        pdf.cell(ls[i], 8, c, 1, 0, 'C', fill=True)
    pdf.ln()
    
    pdf.set_font('Arial', '', 8)
    for _, row in df_f.iterrows():
        pdf.cell(ls[0], 7, row['DATA'].strftime('%d/%m/%Y'), 1, 0, 'C')
        pdf.cell(ls[1], 7, formatar_br(row['LIVRO_RAZAO']), 1, 0, 'R')
        pdf.cell(ls[2], 7, formatar_br(row['TOTAL_CARTOES']), 1, 0, 'R')
        pdf.cell(ls[3], 7, formatar_br(row['DIFERENÇA (EM ESPÉCIE)']), 1, 1, 'R')
    
    return pdf.output(dest='S').encode('latin-1', errors='ignore')

# --- LEITURA DO LIVRO RAZÃO ---
def ler_livro_razao(arquivo):
    try:
        nome_arquivo = arquivo.name.lower()
        if nome_arquivo.endswith(('.xlsx', '.xls')):
            try:
                df = pd.read_excel(arquivo, header=None)
            except:
                try:
                    arquivo.seek(0)
                    df = pd.read_excel(arquivo, header=None, engine='xlrd')
                except:
                    try:
                        arquivo.seek(0)
                        df = pd.read_excel(arquivo, header=None, engine='openpyxl')
                    except:
                        return None
        elif nome_arquivo.endswith('.csv'):
            conteudo = arquivo.read()
            arquivo.seek(0)
            df = None
            for sep in [',', ';', '\t']:
                for encoding in ['utf-8', 'latin-1', 'cp1252', 'iso-8859-1']:
                    try:
                        df = pd.read_csv(io.BytesIO(conteudo), sep=sep, encoding=encoding, header=None, on_bad_lines='skip', quotechar='"')
                        if len(df.columns) > 3: break
                    except: continue
                if df is not None and len(df.columns) > 3: break
            if df is None: return None
        else: return None

        padroes_data = ['DATA', 'DT', 'DT.', 'DIA', 'DATE']
        padroes_historico = ['HISTÓRICO', 'HISTORICO', 'DESCRIÇÃO', 'DESCRICAO', 'HIST']
        padroes_debito = ['DÉBITO', 'DEBITO', 'DÉB', 'DEB', 'D']
        
        col_data, col_historico, col_debito = None, None, None
        for row_idx in range(min(10, len(df))):
            for col_idx in df.columns:
                valor = str(df.iloc[row_idx, col_idx]).upper().strip()
                if any(padrao == valor or valor.startswith(padrao) for padrao in padroes_data): col_data = col_idx
                elif any(padrao in valor for padrao in padroes_historico): col_historico = col_idx
                elif any(padrao == valor or valor.startswith(padrao) for padrao in padroes_debito): col_debito = col_idx

        if col_data is not None and col_historico is not None and col_debito is not None:
            df_clean = df[[col_data, col_historico, col_debito]].copy()
            df_clean.columns = ['DATA_RAW', 'HISTORICO', 'DEBITO_RAW']
            linha_header = None
            for idx, valor in df_clean['HISTORICO'].items():
                if any(padrao in str(valor).upper() for padrao in padroes_historico):
                    linha_header = idx
                    break
            if linha_header is None: return None
            df_clean = df_clean.iloc[linha_header + 1:].reset_index(drop=True)
            df_clean = df_clean.dropna(subset=['DATA_RAW', 'HISTORICO'], how='all')
            df_clean = df_clean[df_clean['HISTORICO'].astype(str).str.strip() != '']
            df_clean = df_clean.reset_index(drop=True)
            movimentos = []
            for _, row in df_clean.iterrows():
                historico = str(row['HISTORICO']).upper().strip()
                if 'MOVIMENTO' in historico and 'DIA' in historico:
                    data_f = normalizar_data(row['DATA_RAW'])
                    valor_f = limpar_valor(row['DEBITO_RAW'])
                    if data_f is not None and valor_f > 0:
                        movimentos.append({'DATA': data_f, 'LIVRO_RAZAO': valor_f})
            if not movimentos: return None
            df_resultado = pd.DataFrame(movimentos)
            return df_resultado.groupby('DATA')['LIVRO_RAZAO'].sum().reset_index()
        else:
            movimento_col_idx = None
            movimento_row_indices = []
            for col_idx in df.columns:
                for row_idx, valor in df[col_idx].items():
                    if pd.notna(valor):
                        valor_str = str(valor).upper().strip()
                        if 'MOVIMENTO' in valor_str and 'DIA' in valor_str:
                            movimento_col_idx = col_idx
                            movimento_row_indices.append(row_idx)
            if movimento_col_idx is None: return None
            data_col_idx = movimento_col_idx - 1 if movimento_col_idx > 0 else None
            debito_col_idx = None
            for i in range(movimento_col_idx + 1, len(df.columns)):
                valores_amostra = df.iloc[:, i].dropna().head(20)
                valores_convertidos = pd.to_numeric(valores_amostra, errors='coerce')
                if valores_convertidos.notna().sum() / max(len(valores_amostra), 1) > 0.5:
                    debito_col_idx = i
                    break
            if data_col_idx is None or debito_col_idx is None: return None
            movimentos = []
            for row_idx in movimento_row_indices:
                try:
                    data_f = normalizar_data(df.iloc[row_idx, data_col_idx])
                    valor_f = limpar_valor(df.iloc[row_idx, debito_col_idx])
                    if data_f is not None and valor_f > 0:
                        movimentos.append({'DATA': data_f, 'LIVRO_RAZAO': valor_f})
                except: continue
            if not movimentos: return None
            df_resultado = pd.DataFrame(movimentos)
            return df_resultado.groupby('DATA')['LIVRO_RAZAO'].sum().reset_index()
    except Exception as e:
        return None

# --- LEITURA DAS MÁQUINAS (COM CAPTURA DE REJEITADAS) ---
def ler_maquina(arquivo, nome_op, separar_vouchers=True):
    pulos = {
        'VR': 14, 'CIELO': 9, 'SIPAG': 2, 'CABAL': 2, 'PLUXEE': 6,
        'TICKET': 8, 'PAGBANK': 0, 'REDE': 1, 'STONE': 0, 'ALELO': 0,
        'CAIXA': 0, 'CURINGA': 0
    }
    skip = pulos.get(nome_op, 0)
    
    try:
        df = None
        if arquivo.name.endswith('.csv'):
            conteudo = arquivo.read()
            arquivo.seek(0)
            for encoding in ['utf-8', 'latin-1', 'cp1252']:
                for sep in [';', ',', '\t']:
                    try:
                        df_temp = pd.read_csv(io.BytesIO(conteudo), skiprows=skip, sep=sep, encoding=encoding, engine='python', on_bad_lines='skip')
                        if len(df_temp.columns) > 3:
                            df = df_temp
                            break
                    except: pass
                if df is not None: break
            if df is None:
                arquivo.seek(0)
                df = pd.read_csv(arquivo, skiprows=skip, sep=None, engine='python', encoding='latin-1')
        else:
            df = pd.read_excel(arquivo, skiprows=skip)
        
        df.columns = [str(c).strip() for c in df.columns]
        
        def norm_str(texto):
            if pd.isna(texto): return ""
            return unicodedata.normalize('NFKD', str(texto)).encode('ASCII', 'ignore').decode('utf-8').upper().strip()

        df_cols_norm = {c: norm_str(c) for c in df.columns}

        mapeamento_colunas = {
            'ALELO': {'data': ['DATA DA VENDA', 'DATA', 'DATA DA TRANSACAO'], 'valor': ['VALOR BRUTO', 'VALOR'], 'liquido': ['VALOR LIQUIDO', 'VALOR LIQUIDO DA PARCELA', 'VALOR DEPOSITADO']},
            'CABAL': {'data': ['DATA', 'DATA DA VENDA', 'DATA DA TRANSACAO'], 'valor': ['VALOR PARCELA BRUTO'], 'liquido': ['VALOR PARCELA LIQUIDO']},
            'CAIXA': {'data': ['DATA DA TRANSACAO', 'DATA', 'DT VENDA'], 'valor': ['VALOR BRUTO DA PARCELA', 'VALOR BRUTO'], 'liquido': ['VALOR LIQUIDO DA PARCELA/TRANSACAO', 'VALOR LIQUIDO']},
            'CIELO': {'data': ['DATA', 'DATA DA VENDA', 'DT VENDA'], 'valor': ['VALOR BRUTO'], 'liquido': ['VALOR LIQUIDO']},
            'CURINGA': {'data': ['DATA', 'DATA DA VENDA', 'DT VENDA'], 'valor': ['VALOR BRUTO'], 'liquido': ['VALOR LIQUIDO']},
            'PAGBANK': {'data': ['DATA DA TRANSACAO', 'DATA', 'DATA DA VENDA', 'DT VENDA'], 'valor': ['VALOR BRUTO', 'VALOR ORIGINAL'], 'liquido': ['VALOR LIQUIDO', 'VALOR LIQUIDO DA TRANSACAO']},
            'REDE': {'data': ['DATA DA VENDA', 'DATA', 'DATA_DA_VENDA'], 'valor': ['VALOR DA VENDA ATUALIZADO'], 'liquido': ['VALOR LIQUIDO']},
            'SIPAG': {'data': ['DATA', 'DATA DA VENDA', 'DATA DA TRANSACAO', 'DT VENDA'], 'valor': ['VALOR PARCELA BRUTO'], 'liquido': ['VALOR PARCELA LIQUIDO']},
            'TICKET': {'data': ['DATA DA TRANSACAO', 'DATA', 'DATA DA VENDA'], 'valor': ['VL TRANSACAO', 'VALOR BRUTO', 'VALOR', 'VL']}
        }
        
        config = mapeamento_colunas.get(nome_op, {'data': ['DATA DA TRANSACAO', 'DATA', 'DATA DA VENDA', 'DT VENDA'], 'valor': ['VALOR BRUTO', 'VALOR', 'TOTAL']})
        
        # Detecta coluna de Valor para podermos somar as rejeitadas antes de descartar
        c_vl = None
        for expected in config['valor']:
            for c_orig, c_n in df_cols_norm.items():
                if expected == c_n: c_vl = c_orig; break
            if c_vl: break
            
        if not c_vl:
            for padrao in ['VALOR BRUTO', 'VALOR ORIGINAL', 'VALOR DA VENDA', 'VALOR', 'TOTAL']:
                for c_orig, c_n in df_cols_norm.items():
                    if c_n == padrao: c_vl = c_orig; break
                if c_vl: break

        # --- FILTRO DE STATUS (SOMANDO VENDAS NÃO APROVADAS) ---
        c_status = None
        for c_orig, c_n in df_cols_norm.items():
            if any(p == c_n for p in ['STATUS', 'STATUS DA VENDA', 'STATUS DA TRANSACAO', 'SITUACAO']):
                c_status = c_orig
                break
        
        vendas_rejeitadas = 0.0
        if c_status:
            mask_invalida = df[c_status].astype(str).str.upper().str.contains('NEGAD|CANCELAD|RECUSAD|REJEITAD|FALHA|DESFEIT', na=False)
            if mask_invalida.any() and c_vl:
                vendas_rejeitadas = df.loc[mask_invalida, c_vl].apply(limpar_valor).sum()
            df = df[~mask_invalida]

        # --- FILTRO DE VOUCHERS ---
        c_mod = None
        for c_orig, c_n in df_cols_norm.items():
            if c_n in ['MODALIDADE', 'PRODUTO', 'TIPO', 'BANDEIRA', 'TIPO DE CARTAO']:
                c_mod = c_orig
                break
                
        if c_mod and separar_vouchers:
            mask_voucher = ~df[c_mod].astype(str).str.upper().str.contains('VOUCHER', na=False)
            df = df[mask_voucher]

        c_dt = None
        for expected in config['data']:
            for c_orig, c_n in df_cols_norm.items():
                if expected == c_n: c_dt = c_orig; break
            if c_dt: break
            
        if not c_dt:
            for padrao in ['DATA DA TRANSACAO', 'DATA DA VENDA', 'DT VENDA', 'DATA', 'DT']:
                for c_orig, c_n in df_cols_norm.items():
                    if c_n == padrao: c_dt = c_orig; break
                if c_dt: break

        c_liq = None
        if 'liquido' in config:
            for expected in config['liquido']:
                for c_orig, c_n in df_cols_norm.items():
                    if expected == c_n: c_liq = c_orig; break
                if c_liq: break
        
        if not c_liq:
            for c_orig, c_n in df_cols_norm.items():
                if 'LIQUIDO' in c_n: c_liq = c_orig; break

        c_mq = None
        for c_orig, c_n in df_cols_norm.items():
            if any(p in c_n for p in ['MAQUINA', 'OPERADORA', 'NOME', 'EQUIPAMENTO', 'PDV', 'EC', 'ESTABELECIMENTO']):
                c_mq = c_orig; break
        
        if not c_dt or not c_vl: return None, 0.0
        
        df['DATA_OK'] = df[c_dt].apply(normalizar_data)
        df['VALOR_OK'] = df[c_vl].apply(limpar_valor)
        
        df = df[df['VALOR_OK'] > 0]
        
        if c_liq and c_liq in df.columns:
            df['VALOR_LIQ_OK'] = df[c_liq].apply(limpar_valor)
            df['DESPESA_OK'] = df['VALOR_OK'] - df['VALOR_LIQ_OK']
            df['DESPESA_OK'] = df['DESPESA_OK'].apply(lambda x: x if x > 0 else 0.0)
        else:
            df['DESPESA_OK'] = 0.0
            df['VALOR_LIQ_OK'] = df['VALOR_OK']
        
        if nome_op == 'CURINGA' and c_mq:
            df['NOME_FINAL'] = df[c_mq].astype(str).str.upper()
        else:
            df['NOME_FINAL'] = nome_op
        
        return df.dropna(subset=['DATA_OK']), vendas_rejeitadas
        
    except Exception as e:
        return None, 0.0

# --- INTERFACE E PROCESSAMENTO ---
with st.sidebar:
    # --- CARD CRESCERE (EXATO CONFORME PRINT) ---
    hoje = datetime.utcnow() - timedelta(hours=3) # Trava o fuso horário no Brasil (UTC-3)
    data_str = hoje.strftime('%d/%m/%Y')
    dias_semana = ['Segunda-feira', 'Terça-feira', 'Quarta-feira', 'Quinta-feira', 'Sexta-feira', 'Sábado', 'Domingo']
    dia_str = dias_semana[hoje.weekday()]
    
    st.markdown(f"""
    <div style="text-align: center; margin-bottom: 20px;">
        <p style="margin: 0; color: #6c757d; font-size: 16px;">{dia_str}</p>
        <p style="margin: 0; color: #0056b3; font-size: 16px; font-weight: bold;">{data_str}</p>
        <br>
        <h3 style="margin: 0; color: #0056b3; font-size: 20px; font-weight: bold;">CRESCERE</h3>
    </div>
    """, unsafe_allow_html=True)

    st.header("📂 Configurações")
    nome_empresa = st.text_input("🏢 Nome da Empresa", value="", placeholder="Digite o nome da empresa")
    
    st.divider()
    st.header("⚙️ Regras de Negócio")
    separar_vouchers = st.toggle(
        "Separar Vouchers das Operadoras?",
        value=True,
        help="LIGADO: Ignora Vale Alimentação nas planilhas das operadoras (você deve subir a planilha do VR separada). DESLIGADO: Aceita tudo junto."
    )
    
    st.divider()
    st.header("📂 Arquivos")
    f_razao = st.file_uploader("Livro Razão", type=['csv', 'xlsx', 'xls'])
    st.divider()
    op_list = ['SIPAG', 'ALELO', 'PLUXEE', 'VR', 'TICKET', 'CABAL', 'PAGBANK', 'CIELO', 'CURINGA', 'CAIXA', 'REDE']
    ups = {n: st.file_uploader(n, type=['xlsx', 'csv', 'xls']) for n in op_list}
    btn = st.button("🚀 Processar Conciliação", use_container_width=True, type="primary")

st.title("🛡️ CONCILIACAO FATURAMENTO vs CARTAO DE CREDITO")

if btn:
    t_start = time.time() # INICIA O CRONÔMETRO
    if f_razao:
        df_r = ler_livro_razao(f_razao)
        if df_r is not None:
            st.session_state.bases_ajustadas = {}
            maqs_detalhe = {}
            lista_resumos = []
            detalhes_pix = {}
            st.session_state.total_vendas_rejeitadas = 0.0
            
            for n, f in ups.items():
                if f:
                    resultado_leitura = ler_maquina(f, n, separar_vouchers)
                    if resultado_leitura is not None and resultado_leitura[0] is not None:
                        base, rejeitadas = resultado_leitura
                        st.session_state.total_vendas_rejeitadas += rejeitadas
                        
                        total, pix, sem_pix = extrair_valor_pix(base, n)
                        despesa_total = base['DESPESA_OK'].sum() if 'DESPESA_OK' in base.columns else 0
                        
                        detalhes_pix[n] = {'total': total, 'pix': pix, 'sem_pix': sem_pix, 'despesa': despesa_total}
                        
                        for mq in base['NOME_FINAL'].unique():
                            sub = base[base['NOME_FINAL'] == mq].copy()
                            maqs_detalhe[mq] = sub
                            res = sub.groupby('DATA_OK')['VALOR_OK'].sum().reset_index()
                            res.columns = ['DATA', f'VALOR_{mq}']
                            lista_resumos.append(res)
            
            if lista_resumos:
                df_c = lista_resumos[0]
                for d in lista_resumos[1:]: df_c = pd.merge(df_c, d, on='DATA', how='outer')
                cols_v = [c for c in df_c.columns if 'VALOR_' in c]
                df_c['TOTAL_CARTOES'] = df_c[cols_v].sum(axis=1)
                final = pd.merge(df_r, df_c[['DATA', 'TOTAL_CARTOES']], on='DATA', how='outer').fillna(0)
            else:
                final = df_r.copy(); final['TOTAL_CARTOES'] = 0.0
            
            final['DIFERENÇA (EM ESPÉCIE)'] = final['LIVRO_RAZAO'] - final['TOTAL_CARTOES']
            final['MES_REF'] = final['DATA'].dt.strftime('%m/%Y')
            st.session_state.base_consolidada = final
            st.session_state.maquinas_detalhado = maqs_detalhe
            st.session_state.detalhes_pix = detalhes_pix
            st.session_state.nome_empresa = nome_empresa
            
            # FINALIZA O CRONÔMETRO
            t_end = time.time()
            st.session_state.tempo_proc = t_end - t_start
            st.session_state.t_start_erp = time.time()
            
    else: st.warning("Suba o Razão.")

# --- EXIBIÇÃO E EXPORTAÇÃO ---
if st.session_state.base_consolidada is not None:
    # Exibe a notificação de tempo de processamento
    st.success(f"✅ Bases consolidadas com sucesso em {st.session_state.tempo_proc:.2f} segundos!")

    df = st.session_state.base_consolidada
    meses = sorted(df['MES_REF'].unique(), key=lambda x: datetime.strptime(x, '%m/%Y'), reverse=True)
    
    meses_formatados = {datetime.strptime(m, '%m/%Y').strftime('%b/%Y').upper(): m for m in meses}
    
    st.divider()
    st.subheader("📅 Seleção de Período para Exibição")
    meses_selecionados_nomes = st.multiselect(
        "Selecione os meses que deseja analisar nas tabelas abaixo:",
        options=list(meses_formatados.keys()),
        default=list(meses_formatados.keys())[0] if meses_formatados else []
    )
    
    meses_selecionados = [meses_formatados[m] for m in meses_selecionados_nomes]
    df_f = df[df['MES_REF'].isin(meses_selecionados)] if meses_selecionados else df.copy()
    
    # ORDENAÇÃO CRESCENTE
    df_f = df_f.sort_values('DATA', ascending=True)
    
    total_livro = df_f['LIVRO_RAZAO'].sum()
    total_maquinas = df_f['TOTAL_CARTOES'].sum()
    total_especie = df_f['DIFERENÇA (EM ESPÉCIE)'].sum()
    
    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Livro Razão", formatar_br(total_livro))
    c2.metric("Total Máquinas", formatar_br(total_maquinas))
    c3.metric("Em Espécie", formatar_br(total_especie))
    # Nova Métrica
    c4.metric("Vendas Não Aprovadas", formatar_br(st.session_state.total_vendas_rejeitadas))
    
    if 'maquinas_detalhado' in st.session_state and st.session_state.maquinas_detalhado:
        st.subheader("💳 Detalhamento por Máquina")
        maquinas_info = []
        for nome_maquina, df_maquina in st.session_state.maquinas_detalhado.items():
            df_filtrado = df_maquina[df_maquina['DATA_OK'].dt.strftime('%m/%Y').isin(meses_selecionados)] if meses_selecionados else df_maquina
            total_maquina = df_filtrado['VALOR_OK'].sum()
            
            if total_maquina > 0:
                pix_maquina = 0
                if 'CURINGA' in nome_maquina or any(op in nome_maquina for op in ['CACHORRO', 'GATO', 'RAPOSA']):
                    col_pix = detectar_coluna_pix(df_filtrado, 'CURINGA')
                    if col_pix and col_pix in df_filtrado.columns:
                        mask_pix = df_filtrado[col_pix].astype(str).str.upper().str.contains('PIX', na=False)
                        pix_maquina = df_filtrado.loc[mask_pix, 'VALOR_OK'].sum()
                
                maquinas_info.append({'nome': nome_maquina, 'total': total_maquina, 'pix': pix_maquina, 'sem_pix': total_maquina - pix_maquina})
        
        maquinas_info.sort(key=lambda x: x['total'], reverse=True)
        cols = st.columns(3)
        for idx, info in enumerate(maquinas_info):
            with cols[idx % 3]:
                if info['pix'] > 0:
                    st.metric(f"{info['nome']}", formatar_br(info['sem_pix']), f"PIX: {formatar_br(info['pix'])}")
                else:
                    st.metric(f"{info['nome']}", formatar_br(info['total']))

    if len(meses_selecionados) == 1:
        st.divider()
        st.subheader("⚖️ Auditoria Contábil do Mês")
        if total_maquinas > total_livro:
            st.markdown(
                """<div style="background-color: #fdf2f2; border-left: 5px solid #d9534f; color: #a94442; padding: 15px; border-radius: 5px; margin-bottom: 20px;">
                    <strong>⚠️ Possível omissão de receita.</strong><br>O valor total vendido nos cartões neste mês é superior ao montante declarado no Livro Razão. A ferramenta de redistribuição de saldos não está disponível para este cenário.
                </div>""", unsafe_allow_html=True)
        else:
            if (df_f['DIFERENÇA (EM ESPÉCIE)'] < -0.01).any():
                st.markdown(
                    """<div style="background-color: #f0fdf4; border-left: 5px solid #4ade80; color: #166534; padding: 15px; border-radius: 5px; margin-bottom: 15px;">
                        <strong>✅ Faturamento Suficiente Detectado</strong><br>O Livro Razão do mês cobre as vendas de cartão, mas existem dias com diferença negativa. Você pode redistribuir os saldos para bater as datas.
                    </div>""", unsafe_allow_html=True)
                
                if st.button("🛠️ Redistribuir Valores (Buscar dia positivo mais próximo)"):
                    df_ajustado = redistribuir_saldos(df_f)
                    st.session_state.bases_ajustadas[meses_selecionados[0]] = df_ajustado
                    st.success("Saldos redistribuídos com sucesso! Verifique a tabela ajustada abaixo ou baixe o PDF Redistribuído na aba.")
            else:
                st.info("Todos os dias deste mês já possuem saldo em espécie positivo ou zerado. Nenhuma redistribuição necessária.")
    
    st.divider()
    tab1, tab2, tab3, tab4 = st.tabs(["📋 Tabela", "📊 Resumo Mensal", "📄 Gerar PDF", "📥 Exportar Excel (ERP)"])
    
    with tab1:
        st.subheader("📊 Situação Real (Original)")
        
        # FORMATAÇÃO PARA REMOVER O 00:00:00 NA EXIBIÇÃO
        style_real = df_f.drop(columns=['MES_REF']).style.format({
            'DATA': lambda x: x.strftime('%d/%m/%Y') if pd.notnull(x) else '',
            'LIVRO_RAZAO': formatar_br, 
            'TOTAL_CARTOES': formatar_br, 
            'DIFERENÇA (EM ESPÉCIE)': formatar_br
        })
        st.dataframe(style_real, use_container_width=True, hide_index=True)
        
        if len(meses_selecionados) == 1 and meses_selecionados[0] in st.session_state.bases_ajustadas:
            st.divider()
            st.subheader("✨ Situação Ajustada (Após Redistribuição)")
            
            df_ajustado = st.session_state.bases_ajustadas[meses_selecionados[0]]
            # FORMATAÇÃO PARA REMOVER O 00:00:00 NA EXIBIÇÃO
            style_ajustado = df_ajustado.drop(columns=['MES_REF']).style.format({
                'DATA': lambda x: x.strftime('%d/%m/%Y') if pd.notnull(x) else '',
                'LIVRO_RAZAO': formatar_br, 
                'TOTAL_CARTOES': formatar_br, 
                'DIFERENÇA (EM ESPÉCIE)': formatar_br
            })
            st.dataframe(style_ajustado, use_container_width=True, hide_index=True)
        
    with tab2:
        res_m = df.groupby('MES_REF').sum(numeric_only=True).reset_index()
        st.dataframe(res_m.style.format({'LIVRO_RAZAO': formatar_br, 'TOTAL_CARTOES': formatar_br, 'DIFERENÇA (EM ESPÉCIE)': formatar_br}), use_container_width=True, hide_index=True)
        
    with tab3:
        st.subheader("📄 Configuração do Relatório PDF")
        meses_pdf_nomes = st.multiselect("Mês a exportar para PDF (Vazio = IMPRIMIR TODOS):", options=list(meses_formatados.keys()), default=meses_selecionados_nomes, key="multi_pdf")
        meses_pdf = [meses_formatados[m] for m in meses_pdf_nomes] if meses_pdf_nomes else list(meses_formatados.values())
        df_pdf = df[df['MES_REF'].isin(meses_pdf)].sort_values('DATA', ascending=False)
        
        if FPDF_INSTALADO and meses_pdf:
            res_pdf, det_pix_pdf = {}, {}
            for mq, b in st.session_state.maquinas_detalhado.items():
                mask = b['DATA_OK'].dt.strftime('%m/%Y').isin(meses_pdf)
                if b.loc[mask, 'VALOR_OK'].sum() > 0:
                    t, p, sp = extrair_valor_pix(b[mask], mq)
                    res_pdf[mq] = b.loc[mask, 'VALOR_OK'].sum()
                    det_pix_pdf[mq] = {'total': t, 'pix': p, 'sem_pix': sp, 'despesa': b.loc[mask, 'DESPESA_OK'].sum() if 'DESPESA_OK' in b.columns else 0}
            
            pdf_data_real = gerar_pdf_final(df_pdf, res_pdf, det_pix_pdf, meses_pdf, df_pdf['LIVRO_RAZAO'].sum(), st.session_state.get('nome_empresa', ''), tipo_relatorio="REAL")
            if pdf_data_real:
                st.download_button(
                    label="📥 Baixar PDF - SITUAÇÃO REAL" if not meses_pdf_nomes else f"📥 Baixar PDF - SITUAÇÃO REAL ({formatar_periodo(meses_pdf)})",
                    data=pdf_data_real,
                    file_name="DRM_SITUACAO_REAL_COMPLETA.pdf" if not meses_pdf_nomes else f"DRM_SITUACAO_REAL_{meses_pdf[0].replace('/', '_')}.pdf",
                    mime="application/pdf", use_container_width=True
                )
            
            if len(meses_pdf) == 1 and meses_pdf[0] in st.session_state.bases_ajustadas:
                df_ajustado_pdf = st.session_state.bases_ajustadas[meses_pdf[0]]
                st.markdown("<br>⬇️ **Relatório Corrigido Disponível:**", unsafe_allow_html=True)
                pdf_data_ajustado = gerar_pdf_final(df_ajustado_pdf, res_pdf, det_pix_pdf, meses_pdf, df_ajustado_pdf['LIVRO_RAZAO'].sum(), st.session_state.get('nome_empresa', ''), tipo_relatorio="AJUSTADO")
                if pdf_data_ajustado:
                    st.download_button(
                        label=f"📥 Baixar PDF - SITUAÇÃO REDISTRIBUÍDA ({formatar_periodo(meses_pdf)})",
                        data=pdf_data_ajustado,
                        file_name=f"DRM_SITUACAO_REDISTRIBUIDA_{meses_pdf[0].replace('/', '_')}.pdf",
                        mime="application/pdf", use_container_width=True, type="primary"
                    )
            
    with tab4:
        st.subheader("📥 Exportação para ERP (Caixa e Despesas)")
        
        # CRONÔMETRO REAJUSTADO PARA MAIOR VISIBILIDADE
        if 't_start_erp' in st.session_state and st.session_state.t_start_erp is not None:
            tempo_decorrido = time.time() - st.session_state.t_start_erp
            minutos = int(tempo_decorrido // 60)
            segundos = int(tempo_decorrido % 60)
            st.info(f"⏱️ Tempo desde o processamento das bases: {minutos}m e {segundos}s")

        st.write("Gera o arquivo Excel contendo os lançamentos de Caixa (Conta 35) e Despesas (Conta 7014).")
        
        meses_xls_nomes = st.multiselect("Mês a exportar para Excel:", options=list(meses_formatados.keys()), default=meses_selecionados_nomes, key="multi_xls")
        meses_xls = [meses_formatados[m] for m in meses_xls_nomes] if meses_xls_nomes else list(meses_formatados.values())
        
        dados_erp = []
        
        for mes in meses_xls:
            df_mes_check = st.session_state.bases_ajustadas[mes] if mes in st.session_state.bases_ajustadas else df[df['MES_REF'] == mes]
            
            total_livro_check = df_mes_check['LIVRO_RAZAO'].sum()
            total_cartoes_check = df_mes_check['TOTAL_CARTOES'].sum()
            
            # --- LOGICA INTELIGENTE DE BLOQUEIO DO CAIXA (Espécie) ---
            ignorar_caixa = False
            
            if total_cartoes_check > total_livro_check:
                ignorar_caixa = True
                st.warning(f"⚠️ **Mês {mes}: Omissão de receita detectada.** O lançamento da diferença em espécie (Caixa - Conta 35) não será enviado ao ERP. Apenas as despesas de cartão (Conta 7014) serão exportadas.")
                
            elif (df_mes_check['DIFERENÇA (EM ESPÉCIE)'] < -0.01).any():
                ignorar_caixa = True
                st.warning(f"⚠️ **Mês {mes}: Saldos negativos encontrados (necessário redistribuir).** O lançamento do Caixa (Conta 35) não será enviado. Apenas as despesas (Conta 7014) serão exportadas.")
        
            # 1. LANÇAMENTOS DE ESPÉCIE (CAIXA 35 / CARTÃO 1071)
            # Só executa se não houver bloqueio local
            if not ignorar_caixa:
                for _, row in df_mes_check.iterrows():
                    val_especie = row['DIFERENÇA (EM ESPÉCIE)']
                    if round(val_especie, 2) > 0.00:
                        dados_erp.append({
                            'Lancto Aut.': '', 'Debito': 35, 'Credito': 1071,
                            'Data': row['DATA'].strftime('%d/%m/%Y'), 'Valor': round(val_especie, 2),
                            'Cod. Historico': 31, 'Historico': '',
                            'Ccusto Debito': '', 'Ccusto Credito': '', 'Nr.Documento': '', 'Complemento': ''
                        })
                    
        # 2. LANÇAMENTOS DE DESPESAS (TAXAS 7014 / CARTÃO 1071)
        # As despesas SEMPRE sobem, independente dos bloqueios do caixa
        for mq, b in st.session_state.maquinas_detalhado.items():
            if 'DESPESA_OK' in b.columns:
                mask = b['DATA_OK'].dt.strftime('%m/%Y').isin(meses_xls)
                b_filtrado = b[mask]
                
                resumo_diario = b_filtrado.groupby('DATA_OK')['DESPESA_OK'].sum().reset_index()
                resumo_diario['DESPESA_OK'] = resumo_diario['DESPESA_OK'].round(2)
                resumo_diario = resumo_diario[resumo_diario['DESPESA_OK'] > 0.00]
                
                for _, row in resumo_diario.iterrows():
                    dados_erp.append({
                        'Lancto Aut.': '', 'Debito': 7014, 'Credito': 1071,
                        'Data': row['DATA_OK'].strftime('%d/%m/%Y'), 'Valor': row['DESPESA_OK'],
                        'Cod. Historico': 201, 'Historico': mq,
                        'Ccusto Debito': '', 'Ccusto Credito': '', 'Nr.Documento': '', 'Complemento': ''
                    })
        
        if dados_erp:
            df_export = pd.DataFrame(dados_erp)
            df_export['DATA_SORT'] = pd.to_datetime(df_export['Data'], format='%d/%m/%Y')
            df_export = df_export.sort_values(['DATA_SORT', 'Debito']).drop(columns=['DATA_SORT'])
            
            st.dataframe(df_export.style.format({'Valor': formatar_br}), use_container_width=True, hide_index=True)
            
            buffer = io.BytesIO()
            with pd.ExcelWriter(buffer, engine='openpyxl') as writer:
                df_export.to_excel(writer, index=False, sheet_name='Planilha1')
                
            st.download_button(
                label="📥 Baixar Excel ERP (Base Completa)" if not meses_xls_nomes else "📥 Baixar Excel ERP",
                data=buffer.getvalue(),
                file_name=f"Lançamentos_Contabeis_ERP.xlsx",
                mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                type="primary"
            )
        else:
            st.info("Nenhum lançamento válido para exportação neste cenário.")
