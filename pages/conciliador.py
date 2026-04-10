import streamlit as st
import pandas as pd
import pdfplumber
import mysql.connector
import io
import re
import unicodedata
from thefuzz import fuzz
from ofxparse import OfxParser
import logging
import json

# Configuração de logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

# --- CLASSE UNDO STACK ---
class UndoStack:
    def __init__(self):
        if 'undo_stack' not in st.session_state:
            st.session_state.undo_stack = []

    def push(self, action_type, data):
        """Adiciona uma ação à pilha de desfazer."""
        st.session_state.undo_stack.append({'type': action_type, 'data': data})
        logging.info(f"Ação '{action_type}' adicionada à pilha de desfazer.")

    def pop(self):
        """Remove e retorna a última ação da pilha."""
        if st.session_state.undo_stack:
            action = st.session_state.undo_stack.pop()
            logging.info(f"Ação '{action['type']}' removida da pilha de desfazer.")
            return action
        return None

    def clear(self):
        """Limpa a pilha de desfazer."""
        st.session_state.undo_stack = []
        logging.info("Pilha de desfazer limpa.")

    def is_empty(self):
        """Verifica se a pilha está vazia."""
        return not bool(st.session_state.undo_stack)

# Inicializa a pilha de desfazer
undo_manager = UndoStack()


# --- 1. UTILITÁRIOS E CONEXÃO ---
def get_connection():
    """Estabelece e retorna uma conexão com o banco de dados MySQL."""
    try:
        conn = mysql.connector.connect(
            host=st.secrets["mysql"]["host"],
            user=st.secrets["mysql"]["user"],
            password=st.secrets["mysql"]["password"],
            database=st.secrets["mysql"]["database"],
            use_pure=True,
            ssl_disabled=True
        )
        return conn
    except mysql.connector.Error as err:
        logging.error(f"Erro ao conectar ao MySQL: {err}")
        st.error(f"Erro ao conectar ao banco de dados: {err}")
        st.stop() # Interrompe a execução do app se não conseguir conectar

def padronizar_texto(texto):
    """Remove acentos, converte para maiúsculas e limpa espaços extras."""
    if not texto: return ""
    texto_sem_acento = unicodedata.normalize('NFKD', str(texto)).encode('ASCII', 'ignore').decode('utf-8')
    texto_limpo = re.sub(r'\s+', ' ', texto_sem_acento.upper().strip())
    return texto_limpo

def formatar_moeda(valor):
    """Formata um valor numérico para o padrão monetário brasileiro."""
    return f"R$ {valor:,.2f}".replace(',', 'X').replace('.', ',').replace('X', '.')

def limpar_cnpj(cnpj_str):
    """Remove caracteres não numéricos de um CNPJ."""
    if not cnpj_str: return ""
    return re.sub(r'[^0-9]', '', cnpj_str)

def formatar_cnpj(cnpj_limpo):
    """Formata um CNPJ limpo (apenas números) para o padrão XX.XXX.XXX/XXXX-XX."""
    if not cnpj_limpo or len(cnpj_limpo) != 14:
        return cnpj_limpo
    return f"{cnpj_limpo[:2]}.{cnpj_limpo[2:5]}.{cnpj_limpo[5:8]}/{cnpj_limpo[8:12]}-{cnpj_limpo[12:]}"

def formatar_contraparte_display(empresa_data):
    """Formata os dados da empresa para exibição na contraparte."""
    if empresa_data is None:
        return "Empresa Não Identificada"
    cnpj_formatado = formatar_cnpj(limpar_cnpj(empresa_data['cnpj']))
    return f"{empresa_data['nome']} | {empresa_data['tipo']} | {cnpj_formatado}"


# --- 2. INTELIGÊNCIA: AUTO-LEITURA DE CNPJ E BANCO NO CABEÇALHO ---
# BBOXs aproximados para o cabeçalho do PDF (ajuste conforme seus extratos)
BBOX_HEADER_AREA = (0, 0, 600, 150) # Área geral do cabeçalho
BBOX_CNPJ_AREA = (50, 50, 300, 100) # Área mais provável para o CNPJ
BBOX_BANK_NAME_AREA = (50, 0, 550, 150) # Área mais ampla para o nome do banco

BANCOS_KEYWORDS = {
    'STONE': ['STONE', 'INSTITUIÇÃO DE PAGAMENTO'],
    'SICOOB': ['SICOOB', 'BANCOOB', 'SICOOB BANCO'],
    'BRADESCO': ['BRADESCO', 'BANCO BRADESCO'],
    'ITAU': ['ITAU', 'BANCO ITAU'],
    'CAIXA': ['CAIXA', 'CAIXA ECONOMICA FEDERAL'],
    'SANTANDER': ['SANTANDER', 'BANCO SANTANDER'],
    'BB': ['BANCO DO BRASIL', 'BB'],
    'NUBANK': ['NUBANK', 'NU PAGAMENTOS'],
    # Adicione mais bancos e suas palavras-chave
}

def identificar_cnpj_no_pdf(file_bytes):
    """Busca CNPJ na área do cabeçalho do PDF."""
    try:
        with pdfplumber.open(io.BytesIO(file_bytes)) as pdf:
            if len(pdf.pages) > 0:
                # Extrai texto apenas da área do cabeçalho da primeira página
                header_text = pdf.pages[0].crop(BBOX_HEADER_AREA).extract_text()
                if not header_text: return None
                # Busca CNPJ no texto extraído
                cnpj_match = re.search(r'\d{2}\.?\d{3}\.?\d{3}/?\d{4}-?\d{2}', header_text)
                if cnpj_match:
                    cnpj_lido = cnpj_match.group(0)
                    logging.info(f"CNPJ '{cnpj_lido}' identificado no PDF.")
                    return cnpj_lido
    except Exception as e:
        logging.error(f"Erro ao identificar CNPJ no PDF: {e}")
    return None

def identificar_banco_no_pdf(file_bytes):
    """Busca o nome do banco na área do cabeçalho do PDF."""
    try:
        with pdfplumber.open(io.BytesIO(file_bytes)) as pdf:
            if len(pdf.pages) > 0:
                # Extrai texto da área do cabeçalho para identificar o banco
                header_text = pdf.pages[0].crop(BBOX_BANK_NAME_AREA).extract_text()
                if not header_text: return "DESCONHECIDO"
                header_text_upper = padronizar_texto(header_text)
                for banco, keywords in BANCOS_KEYWORDS.items():
                    for keyword in keywords:
                        if padronizar_texto(keyword) in header_text_upper:
                            logging.info(f"Banco '{banco}' identificado no PDF.")
                            return banco
    except Exception as e:
        logging.error(f"Erro ao identificar banco no PDF: {e}")
    return "DESCONHECIDO"

@st.cache_data(show_spinner=False)
def buscar_empresa_por_cnpj_otimizado(cnpj_formatado, df_empresas):
    """Busca a empresa no DataFrame de empresas pelo CNPJ."""
    if not cnpj_formatado:
        return None
    cnpj_limpo_buscado = limpar_cnpj(cnpj_formatado)
    # Garante que a coluna 'cnpj_limpo' exista no df_empresas para comparação
    if 'cnpj_limpo' not in df_empresas.columns:
        df_empresas['cnpj_limpo'] = df_empresas['cnpj'].astype(str).apply(limpar_cnpj)
    
    match_df = df_empresas[df_empresas['cnpj_limpo'] == cnpj_limpo_buscado]
    if not match_df.empty:
        empresa_data = match_df.iloc[0].to_dict()
        logging.info(f"Empresa '{empresa_data['nome']}' encontrada para o CNPJ '{cnpj_formatado}'.")
        return empresa_data
    logging.info(f"Nenhuma empresa encontrada para o CNPJ '{cnpj_formatado}'.")
    return None

@st.cache_data(ttl=60, show_spinner=False)
def buscar_conta_por_banco(id_empresa, nome_banco):
    """Busca a conta contábil específica de uma empresa para um banco."""
    conn = None
    try:
        conn = get_connection()
        cursor = conn.cursor(dictionary=True)
        query = "SELECT conta_contabil FROM empresa_banco_contas WHERE id_empresa = %s AND nome_banco = %s"
        cursor.execute(query, (id_empresa, nome_banco))
        result = cursor.fetchone()
        if result:
            logging.info(f"Conta contábil '{result['conta_contabil']}' encontrada para empresa {id_empresa} no banco {nome_banco}.")
            return result['conta_contabil']
        logging.info(f"Nenhuma conta contábil específica encontrada para empresa {id_empresa} no banco {nome_banco}. Usando padrão da empresa.")
        return None # Retorna None se não encontrar, para usar a conta padrão da empresa
    except mysql.connector.Error as err:
        logging.error(f"Erro ao buscar conta por banco: {err}")
        st.error(f"Erro ao buscar conta por banco: {err}")
        return None
    finally:
        if conn:
            conn.close()


# --- 3. MOTOR DE EXTRAÇÃO PDF (BLINDADO) ---
@st.cache_data(show_spinner=False)
def extrair_por_recintos(file_bytes):
    """
    Extrai transações de um PDF, com regex aprimorada para a terceira coluna.
    Assume uma estrutura de linha: DATA TIPO DESCRIÇÃO VALOR SALDO CONTRAPARTE
    """
    texto_completo = ""
    with pdfplumber.open(io.BytesIO(file_bytes)) as pdf:
        for page in pdf.pages:
            # Usar extract_words e agrupar por proximidade Y para formar linhas
            # Isso é mais robusto que extract_text para layouts variados
            palavras = page.extract_words(x_tolerance=3, y_tolerance=3)
            linhas_dict = {}
            for p in palavras:
                y = round(float(p['top']))
                # Agrupa palavras que estão na mesma "linha" (y_tolerance)
                encontrou_y = next((k for k in linhas_dict if abs(k - y) <= 3), None)
                if encontrou_y is not None:
                    linhas_dict[encontrou_y].append(p)
                else:
                    linhas_dict[y] = [p]
            for y_key in sorted(linhas_dict.keys()):
                linha_ordenada = sorted(linhas_dict[y_key], key=lambda x: x['x0'])
                texto_completo += " ".join([w['text'] for w in linha_ordenada]) + "\n"

    RUIDO_CABECALHO = [
        "período:", "página", "saldo anterior", "saldo atual",
        "saldo final", "cnpj", "emitido em", "extrato de conta",
        "dados da conta", "nome documento", "instituição agência", "contraparte stone"
    ]
    
    linhas = [l.strip() for l in texto_completo.split('\n') if l.strip()]
    dados, ignoradas_raw = [], []
    
    for linha in linhas:
        if any(x in linha.lower() for x in RUIDO_CABECALHO):
            continue

        # REGEX APRIMORADA:
        # 1. Captura a data (DD/MM/AA ou DD/MM/AAAA)
        # 2. Captura o tipo de operação (Saída, Entrada, etc.) - grupo nomeado 'tipo_op'
        # 3. Captura a descrição (qualquer coisa, não-ganancioso) - grupo nomeado 'descricao'
        # 4. Captura o valor monetário (R$ 1.000,00 ou 1.000,00) - grupo nomeado 'valor_op'
        match = re.search(
            r'(?P<data>\d{2}/\d{2}/\d{2,4})\s+'
            r'(?P<tipo_op>\S+)\s+'
            r'(?P<descricao>.*?)\s+'
            r'(?:R\$?\s*)?'
            r'(?P<valor_op>\d{1,3}(?:\.\d{3})*,\d{2})',
            linha,
            re.IGNORECASE
        )
        if match:
            data = match.group('data')
            tipo = match.group('tipo_op')
            desc_limpa = match.group('descricao').strip()
            valor_num = float(match.group('valor_op').replace('.', '').replace(',', '.'))
            sinal = '+' if tipo.lower() in ['entrada', 'depósito', 'recebimento'] else '-'
            if not desc_limpa or len(desc_limpa) < 2:
                desc_limpa = tipo.upper() # Usa o tipo como descrição se a descrição estiver vazia
            dados.append({'Data': data, 'Descricao': padronizar_texto(desc_limpa), 'Valor': abs(valor_num), 'Sinal': sinal})
        elif len(linha) > 8: # Linhas que não são ruído e não foram extraídas pela regex
            ignoradas_raw.append(linha)

    ignoradas_unicas = list(dict.fromkeys(ignoradas_raw))
    ignoradas_com_valor = [l for l in ignoradas_unicas if re.search(r'\d,\d{2}', l)]
    ignoradas_texto = [l for l in ignoradas_unicas if not re.search(r'\d,\d{2}', l)]
    logging.info(f"Extração de PDF concluída. {len(dados)} transações e {len(ignoradas_raw)} linhas ignoradas.")
    
    return pd.DataFrame(dados), {"criticas": ignoradas_com_valor, "comuns": ignoradas_texto}

@st.cache_data(show_spinner=False)
def extrair_texto_ofx(file_bytes):
    """Extrai transações de um arquivo OFX."""
    dados_extraidos = []
    try:
        ofx = OfxParser.parse(io.BytesIO(file_bytes))
        for account in ofx.accounts:
            for tx in account.statement.transactions:
                valor = float(tx.amount)
                dados_extraidos.append({
                    'Data': tx.date.strftime('%d/%m/%Y'),
                    'Descricao': padronizar_texto(tx.payee if tx.payee else tx.memo),
                    'Valor': abs(valor),
                    'Sinal': '+' if valor > 0 else '-'
                })
        logging.info(f"Extração de OFX concluída. {len(dados_extraidos)} transações.")
    except Exception as e:
        logging.error(f"Erro ao extrair OFX: {e}")
        st.error(f"Erro ao processar arquivo OFX: {e}")
    
    return pd.DataFrame(dados_extraidos)


# --- 4. CARGA DE DADOS DO BANCO ---
@st.cache_data(ttl=60, show_spinner=False)
def carregar_empresas():
    """Carrega as empresas do banco de dados."""
    conn = None
    try:
        conn = get_connection()
        query = "SELECT id, nome, fantasia, cnpj, tipo, apelido_unidade, conta_contabil FROM empresas WHERE status = 'Ativo'"
        df = pd.read_sql(query, conn)
        df['tipo'] = df['tipo'].fillna('Matriz')
        df['cnpj'] = df['cnpj'].fillna('Sem CNPJ')
        df['cnpj_limpo'] = df['cnpj'].astype(str).apply(limpar_cnpj) # Adiciona CNPJ limpo para busca
        df['display_nome'] = df['nome'] + ' | ' + df['tipo'] + ' | ' + df['cnpj']
        logging.info(f"Carregadas {len(df)} empresas ativas.")
        return df
    except mysql.connector.Error as err:
        logging.error(f"Erro ao carregar empresas: {err}")
        st.error(f"Erro ao carregar empresas: {err}")
        return pd.DataFrame()
    finally:
        if conn:
            conn.close()

@st.cache_data(ttl=60, show_spinner=False)
def carregar_contas_por_banco(id_empresa):
    """Carrega as contas contábeis específicas por banco para uma empresa."""
    conn = None
    try:
        conn = get_connection()
        query = "SELECT id, nome_banco, conta_contabil FROM empresa_banco_contas WHERE id_empresa = %s ORDER BY nome_banco"
        df = pd.read_sql(query, conn, params=(id_empresa,))
        logging.info(f"Carregadas {len(df)} contas por banco para empresa {id_empresa}.")
        return df
    except mysql.connector.Error as err:
        logging.error(f"Erro ao carregar contas por banco para empresa {id_empresa}: {err}")
        st.error(f"Erro ao carregar contas por banco: {err}")
        return pd.DataFrame()
    finally:
        if conn:
            conn.close()


# --- 5. INTERFACE PRINCIPAL ---
st.set_page_config(page_title="Conciliação Bancária", page_icon="🏦", layout="wide")

# VARIÁVEIS DE SESSÃO
if 'skipped_indices' not in st.session_state:
    st.session_state.skipped_indices = []
if 'editando_regra_id' not in st.session_state:
    st.session_state.editando_regra_id = None
if 'editando_conta_banco_id' not in st.session_state:
    st.session_state.editando_conta_banco_id = None
if 'df_bruto' not in st.session_state:
    st.session_state.df_bruto = pd.DataFrame()
if 'banco_detectado' not in st.session_state:
    st.session_state.banco_detectado = "DESCONHECIDO"
if 'empresa_detectada_data' not in st.session_state:
    st.session_state.empresa_detectada_data = None

st.title("🏦 Conciliação Bancária")
df_empresas = carregar_empresas()

# 1º PASSO: UPLOAD DOS ARQUIVOS
uploaded_files = st.file_uploader("1. Arraste seus extratos (PDF ou OFX)", type=["pdf", "ofx"], accept_multiple_files=True)

# 2º PASSO: INTELIGÊNCIA DE PRÉ-SELEÇÃO (CNPJ e Banco)
indice_sugerido = 0
empresa_detectada_data = None
banco_detectado = "DESCONHECIDO"

if uploaded_files:
    for file in uploaded_files:
        if file.name.lower().endswith('.pdf'):
            # Tenta identificar CNPJ no PDF
            cnpj_lido = identificar_cnpj_no_pdf(file.getvalue())
            if cnpj_lido:
                empresa_detectada_data = buscar_empresa_por_cnpj_otimizado(cnpj_lido, df_empresas)
                if empresa_detectada_data:
                    # Encontra o índice da empresa no DataFrame para pré-selecionar no selectbox
                    idx_encontrado = df_empresas[df_empresas['id'] == empresa_detectada_data['id']].index[0]
                    indice_sugerido = int(idx_encontrado)
                    st.toast(f"✅ Extrato da empresa {empresa_detectada_data['nome']} reconhecido pelo CNPJ!")
                    st.session_state.empresa_detectada_data = empresa_detectada_data
                else:
                    st.warning(f"CNPJ '{cnpj_lido}' encontrado no PDF, mas nenhuma empresa correspondente no cadastro.")
            
            # Tenta identificar o banco no PDF
            banco_detectado = identificar_banco_no_pdf(file.getvalue())
            if banco_detectado != "DESCONHECIDO":
                st.toast(f"✅ Banco '{banco_detectado}' identificado no PDF!")
                st.session_state.banco_detectado = banco_detectado
            break # Processa apenas o primeiro PDF para detecção inicial


# 3º PASSO: PAINEL DE CONFIGURAÇÕES
st.markdown("### 2. Confirme os Dados")
col_cfg1, col_cfg2, col_cfg3 = st.columns([2, 1, 1])

# Selectbox de Empresa/Filial
empresa_sel_display = col_cfg1.selectbox("Empresa / Filial", df_empresas['display_nome'], index=indice_sugerido)
empresa_data = df_empresas[df_empresas['display_nome'] == empresa_sel_display].iloc[0].to_dict()
id_empresa = int(empresa_data['id'])

# Selectbox de Banco (pré-selecionado se detectado)
bancos_disponiveis = sorted(list(BANCOS_KEYWORDS.keys()) + [st.session_state.banco_detectado])
bancos_disponiveis = list(dict.fromkeys([b for b in bancos_disponiveis if b != "DESCONHECIDO"])) # Remove duplicatas e desconhecido
if st.session_state.banco_detectado in bancos_disponiveis:
    banco_index = bancos_disponiveis.index(st.session_state.banco_detectado)
else:
    banco_index = 0 # Default para o primeiro banco se não detectado

banco_selecionado = col_cfg2.selectbox("Banco do Extrato", bancos_disponiveis, index=banco_index)

# Busca a conta contábil específica para a empresa e banco selecionados
conta_banco_fixa = buscar_conta_por_banco(id_empresa, banco_selecionado)
if not conta_banco_fixa:
    # Se não encontrar conta específica, usa a conta padrão da empresa
    conta_banco_fixa = empresa_data.get('conta_contabil', 'N/A')
    if conta_banco_fixa == 'N/A':
        st.warning(f"Nenhuma conta contábil padrão definida para a empresa '{empresa_data['nome']}' ou para o banco '{banco_selecionado}'. Por favor, defina.")

col_cfg2.text_input("Conta Banco (Âncora)", value=conta_banco_fixa, disabled=True) # Exibe a conta, mas não permite edição direta
saldo_anterior_informado = col_cfg3.number_input("Saldo Anterior (R$)", value=0.00, step=100.00, format="%.2f")

# 4º PASSO: PROCESSAMENTO REAL
if uploaded_files and conta_banco_fixa != 'N/A':
    if st.button("Processar Extratos"):
        with st.spinner("Lendo e classificando extratos..."):
            lista_dfs, criticas, comuns = [], [], []
            for file in uploaded_files:
                if file.name.lower().endswith('.pdf'):
                    df_ex, ign = extrair_por_recintos(file.getvalue())
                    lista_dfs.append(df_ex)
                    criticas.extend(ign['criticas'])
                    comuns.extend(ign['comuns'])
                else:
                    lista_dfs.append(extrair_texto_ofx(file.getvalue()))
            
            st.session_state.df_bruto = pd.concat(lista_dfs, ignore_index=True) if lista_dfs else pd.DataFrame()
            st.session_state.skipped_indices = [] # Reseta índices pulados ao reprocessar
            undo_manager.clear() # Limpa histórico de ações ao reprocessar
            
            conn = get_connection()
            regras = pd.read_sql(f"SELECT * FROM tb_extratos_regras WHERE id_empresa = {id_empresa} AND banco_nome = '{banco_selecionado}'", conn)
            conn.close()
            
            # --- CLASSIFICAÇÃO E CÁLCULO DINÂMICO DE SALDO ---
            prontos, pendentes = [], []
            linhas_ignoradas_regras = []
            
            if not st.session_state.df_bruto.empty:
                for idx, row in st.session_state.df_bruto.iterrows():
                    match = False
                    for _, r in regras.iterrows():
                        if fuzz.ratio(padronizar_texto(r['termo_chave']), row['Descricao']) >= 85 and r['sinal_esperado'] == row['Sinal']:
                            if r['conta_contabil'] == 'IGNORAR':
                                linhas_ignoradas_regras.append(idx)
                            else:
                                debito_conta = conta_banco_fixa if row['Sinal'] == '+' else r['conta_contabil']
                                credito_conta = r['conta_contabil'] if row['Sinal'] == '+' else conta_banco_fixa
                                prontos.append({
                                    'Debito': debito_conta,
                                    'Credito': credito_conta,
                                    'Data': row['Data'],
                                    'Valor': f"{row['Valor']:.2f}".replace('.', ','),
                                    'Historico': r['historico_padrao'] if r['historico_padrao'] else row['Descricao']
                                })
                            match = True
                            break
                    if not match:
                        pendentes.append({'idx_original': idx, **row})
            
            st.session_state.prontos = prontos
            st.session_state.pendentes = pd.DataFrame(pendentes)
            st.session_state.linhas_ignoradas_regras = linhas_ignoradas_regras
            st.session_state.criticas = criticas
            st.session_state.comuns = comuns
            
            st.success("Processamento concluído!")
            st.rerun() # Força a atualização da interface para mostrar os resultados
else:
    if conta_banco_fixa == 'N/A':
        st.error("Por favor, configure a conta contábil para a empresa e banco selecionados antes de processar.")

if not st.session_state.df_bruto.empty:
    # --- AUDITORIA DE SALDOS (DINÂMICO) ---
    st.divider()
    df_validos = st.session_state.df_bruto[~st.session_state.df_bruto.index.isin(st.session_state.linhas_ignoradas_regras)]
    total_e = df_validos[df_validos['Sinal'] == '+']['Valor'].sum()
    total_s = df_validos[df_validos['Sinal'] == '-']['Valor'].sum()
    saldo_final_calculado = saldo_anterior_informado + total_e - total_s
    
    c_aud1, c_aud2, c_aud3, c_aud4 = st.columns(4)
    c_aud1.metric("Saldo Anterior", formatar_moeda(saldo_anterior_informado))
    c_aud2.metric("🟢 Entradas Válidas", formatar_moeda(total_e))
    c_aud3.metric("🔴 Saídas Válidas", formatar_moeda(total_s))
    c_aud4.metric("⚖️ Saldo Final Calculado", formatar_moeda(saldo_final_calculado))
    
    if st.session_state.criticas or st.session_state.comuns:
        with st.expander(f"⚠️ Alertas de Leitura de PDF ({len(st.session_state.criticas)} críticas / {len(st.session_state.comuns)} informativas)"):
            if st.session_state.criticas:
                st.error("Linhas com valores suspeitos (possíveis transações não lidas):")
                for l in list(dict.fromkeys(st.session_state.criticas)): st.code(l)
            if st.session_state.comuns:
                st.info("Ruídos e textos informativos ignorados:")
                for l in list(dict.fromkeys(st.session_state.comuns))[:20]: st.text(l)

    # --- MESA DE TREINAMENTO ---
    df_p = st.session_state.pendentes
    if not df_p.empty:
        fila = df_p[~df_p['idx_original'].isin(st.session_state.skipped_indices)]
        if not fila.empty:
            item = fila.iloc[0]
            st.subheader("🎓 Mesa de Treinamento")
            
            # CABEÇALHO DO ITEM + BOTÃO DESFAZER
            m1, m2, m3, m4, m5 = st.columns([1, 1, 1, 3, 1])
            m1.metric("📅 Data", item['Data'])
            m2.metric("💰 Valor", formatar_moeda(item['Valor']))
            m3.metric("↕️ Tipo", "🟢 Entrada" if item['Sinal'] == '+' else "🔴 Saída")
            m4.write(f"**Descrição Extraída:** {item['Descricao']}")
            
            # Lógica do Botão Desfazer
            if not undo_manager.is_empty():
                if m5.button("↩️ Desfazer Ação", help="Desfaz a última regra salva, lixo ignorado ou pulo"):
                    ultima_acao = undo_manager.pop()
                    conn = None
                    try:
                        conn = get_connection()
                        cursor = conn.cursor()
                        if ultima_acao['type'] == 'salvar_regra':
                            cursor.execute("DELETE FROM tb_extratos_regras WHERE id = %s", (ultima_acao['data']['id_regra'],))
                            conn.commit()
                            st.toast(f"Regra ID {ultima_acao['data']['id_regra']} desfeita!")
                        elif ultima_acao['type'] == 'ignorar_lixo':
                            cursor.execute("DELETE FROM tb_extratos_regras WHERE id = %s", (ultima_acao['data']['id_regra'],))
                            conn.commit()
                            st.toast(f"Ignorar lixo ID {ultima_acao['data']['id_regra']} desfeito!")
                        elif ultima_acao['type'] == 'pular':
                            if ultima_acao['data']['idx'] in st.session_state.skipped_indices:
                                st.session_state.skipped_indices.remove(ultima_acao['data']['idx'])
                                st.toast("Pulo desfeito!")
                        elif ultima_acao['type'] == 'adicionar_conta_banco':
                            cursor.execute("DELETE FROM empresa_banco_contas WHERE id = %s", (ultima_acao['data']['id_conta'],))
                            conn.commit()
                            st.toast(f"Adição de conta por banco ID {ultima_acao['data']['id_conta']} desfeita!")
                        elif ultima_acao['type'] == 'editar_conta_banco':
                            # Reverte para o estado anterior
                            cursor.execute("UPDATE empresa_banco_contas SET nome_banco = %s, conta_contabil = %s WHERE id = %s",
                                           (ultima_acao['data']['old_nome_banco'], ultima_acao['data']['old_conta_contabil'], ultima_acao['data']['id_conta']))
                            conn.commit()
                            st.toast(f"Edição de conta por banco ID {ultima_acao['data']['id_conta']} desfeita!")
                        elif ultima_acao['type'] == 'deletar_conta_banco':
                            # Reinsere a conta deletada
                            cursor.execute("INSERT INTO empresa_banco_contas (id, id_empresa, nome_banco, conta_contabil) VALUES (%s, %s, %s, %s)",
                                           (ultima_acao['data']['id_conta'], ultima_acao['data']['id_empresa'], ultima_acao['data']['nome_banco'], ultima_acao['data']['conta_contabil']))
                            conn.commit()
                            st.toast(f"Remoção de conta por banco ID {ultima_acao['data']['id_conta']} desfeita!")
                    except mysql.connector.Error as err:
                        logging.error(f"Erro ao desfazer ação: {err}")
                        st.error(f"Erro ao desfazer ação: {err}")
                    finally:
                        if conn:
                            conn.close()
                    st.rerun()

            palavras_desc = item['Descricao'].split()
            selecionadas = st.pills("Selecione os termos-chave (ou deixe vazio para a frase inteira):", palavras_desc, selection_mode="multi")
            termo_final = " ".join(selecionadas) if selecionadas else item['Descricao']
            
            # --- LANÇAMENTOS IMPACTADOS ---
            if termo_final:
                df_impactados = df_p[df_p['Descricao'].str.contains(re.escape(termo_final), case=False, na=False)]
                impacto = len(df_impactados)
                if impacto > 0:
                    st.info(f"💡 Esta regra resolverá **{impacto}** lançamentos desta fila.")
                    with st.expander(f"📋 Ver lançamentos impactados ({impacto})", expanded=False):
                        st.dataframe(
                            df_impactados[['Data', 'Descricao', 'Valor', 'Sinal']].reset_index(drop=True),
                            use_container_width=True
                        )

            with st.form("form_treino"):
                st.caption(f"A regra atuará sobre: **{termo_final}**")
                f1, f2, f3 = st.columns(3)
                contra = f1.text_input("Contrapartida (Conta Contábil)")
                cod_h = f2.text_input("Cód. Hist. (Opcional)")
                txt_h = f3.text_input("Histórico Padrão (Opcional)")
                
                b1, b2, b3, b4 = st.columns(4)
                if b1.form_submit_button("✅ Salvar Regra"):
                    if contra:
                        conn = None
                        try:
                            conn = get_connection()
                            cursor = conn.cursor()
                            query = "INSERT INTO tb_extratos_regras (id_empresa, banco_nome, termo_chave, sinal_esperado, conta_contabil, cod_historico_erp, historico_padrao) VALUES (%s, %s, %s, %s, %s, %s, %s)"
                            cursor.execute(query, (id_empresa, banco_selecionado, termo_final, item['Sinal'], contra, cod_h, txt_h))
                            id_inserido = cursor.lastrowid
                            conn.commit()
                            undo_manager.push('salvar_regra', {'id_regra': id_inserido})
                            st.success("Regra salva com sucesso!")
                        except mysql.connector.Error as err:
                            logging.error(f"Erro ao salvar regra: {err}")
                            st.error(f"Erro ao salvar regra: {err}")
                        finally:
                            if conn:
                                conn.close()
                        st.rerun()
                    else:
                        st.error("Preencha a conta de contrapartida para salvar.")
                
                if b2.form_submit_button("🗑️ Ignorar Lixo"):
                    conn = None
                    try:
                        conn = get_connection()
                        cursor = conn.cursor()
                        query = "INSERT INTO tb_extratos_regras (id_empresa, banco_nome, termo_chave, sinal_esperado, conta_contabil) VALUES (%s, %s, %s, %s, %s)"
                        cursor.execute(query, (id_empresa, banco_selecionado, termo_final, item['Sinal'], 'IGNORAR'))
                        id_inserido = cursor.lastrowid
                        conn.commit()
                        undo_manager.push('ignorar_lixo', {'id_regra': id_inserido})
                        st.success("Regra de ignorar lixo salva!")
                    except mysql.connector.Error as err:
                        logging.error(f"Erro ao ignorar lixo: {err}")
                        st.error(f"Erro ao ignorar lixo: {err}")
                    finally:
                        if conn:
                            conn.close()
                    st.rerun()
                
                if b3.form_submit_button("⏭️ Pular"):
                    st.session_state.skipped_indices.append(item['idx_original'])
                    undo_manager.push('pular', {'idx': item['idx_original']})
                    st.info("Lançamento pulado.")
                    st.rerun()
                
                if b4.form_submit_button("🔄 Resetar Fila"):
                    st.session_state.skipped_indices = []
                    undo_manager.clear()
                    st.info("Fila e histórico de ações resetados.")
                    st.rerun()
    else:
        st.success("🎉 Todos os lançamentos pendentes foram mapeados! Exportação liberada.")
        if st.session_state.prontos:
            df_prontos = pd.DataFrame(st.session_state.prontos)
            st.download_button(
                "📥 BAIXAR CSV ALTERDATA",
                df_prontos.to_csv(index=False, sep=';', encoding='latin1'),
                f"conciliacao_{empresa_data['apelido_unidade']}_{banco_selecionado}_{pd.Timestamp.now().strftime('%Y%m%d%H%M%S')}.csv",
                "text/csv"
            )

# --- GERENCIAMENTO DE REGRAS ---
st.divider()
with st.expander("📚 Gerenciar Regras Cadastradas", expanded=False):
    conn = None
    try:
        conn = get_connection()
        regras_v = pd.read_sql(f"SELECT * FROM tb_extratos_regras WHERE id_empresa = {id_empresa} AND banco_nome = '{banco_selecionado}' ORDER BY id DESC", conn)
        
        busca_regra = st.text_input("🔍 Buscar por termo chave ou conta contábil nas regras...", key="busca_regra")
        if busca_regra:
            regras_v = regras_v[
                regras_v['termo_chave'].str.contains(busca_regra, case=False, na=False) |
                regras_v['conta_contabil'].str.contains(busca_regra, case=False, na=False)
            ]
        
        if not regras_v.empty:
            for _, r in regras_v.iterrows():
                col_r = st.columns([3, 2, 1, 1, 1])
                col_r[0].write(f"**{r['termo_chave']}**")
                col_r[1].write(f"Conta: {r['conta_contabil']} ({r['sinal_esperado']})")
                
                if col_r[2].button("✏️", key=f"e_regra_{r['id']}"):
                    st.session_state.editando_regra_id = r['id']
                    st.rerun()
                if col_r[3].button("🗑️", key=f"d_regra_{r['id']}"):
                    cursor = conn.cursor()
                    cursor.execute("DELETE FROM tb_extratos_regras WHERE id = %s", (int(r['id']),))
                    conn.commit()
                    st.toast("Regra deletada!")
                    st.rerun()
        else:
            st.info("Nenhuma regra encontrada para este banco e empresa.")
    except mysql.connector.Error as err:
        logging.error(f"Erro ao carregar regras: {err}")
        st.error(f"Erro ao carregar regras: {err}")
    finally:
        if conn:
            conn.close()

    # Formulário de Edição de Regra
    if st.session_state.editando_regra_id:
        regra_para_editar = regras_v[regras_v['id'] == st.session_state.editando_regra_id].iloc[0]
        st.subheader(f"Editar Regra ID: {regra_para_editar['id']}")
        
        with st.form(key=f"form_editar_regra_{regra_para_editar['id']}"):
            col_er1, col_er2 = st.columns(2)
            novo_termo = col_er1.text_input("Termo Chave", value=regra_para_editar['termo_chave'])
            nova_conta = col_er2.text_input("Conta Contábil", value=regra_para_editar['conta_contabil'])
            novo_sinal = st.selectbox("Sinal Esperado", ['+', '-'], index=0 if regra_para_editar['sinal_esperado'] == '+' else 1)
            novo_cod_hist = st.text_input("Cód. Histórico Alterdata", value=regra_para_editar['cod_historico_erp'])
            novo_hist_padrao = st.text_area("Histórico Padrão", value=regra_para_editar['historico_padrao'])
            
            if st.form_submit_button("Salvar Edição da Regra"):
                conn = None
                try:
                    conn = get_connection()
                    cursor = conn.cursor()
                    query = "UPDATE tb_extratos_regras SET termo_chave = %s, conta_contabil = %s, sinal_esperado = %s, cod_historico_erp = %s, historico_padrao = %s WHERE id = %s"
                    cursor.execute(query, (novo_termo, nova_conta, novo_sinal, novo_cod_hist, novo_hist_padrao, regra_para_editar['id']))
                    conn.commit()
                    st.success("Regra atualizada com sucesso!")
                    st.session_state.editando_regra_id = None
                except mysql.connector.Error as err:
                    logging.error(f"Erro ao atualizar regra: {err}")
                    st.error(f"Erro ao atualizar regra: {err}")
                finally:
                    if conn:
                        conn.close()
                st.rerun()
            if st.form_submit_button("Cancelar Edição"):
                st.session_state.editando_regra_id = None
                st.rerun()

# --- GERENCIAMENTO DE CONTAS POR BANCO (CRUD) ---
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
                    undo_manager.push('deletar_conta_banco', {'id_conta': c['id'], 'id_empresa': id_empresa, 'nome_banco': c['nome_banco'], 'conta_contabil': c['conta_contabil']})
                    st.toast("Conta por banco deletada!")
                except mysql.connector.Error as err:
                    logging.error(f"Erro ao deletar conta por banco: {err}")
                    st.error(f"Erro ao deletar conta por banco: {err}")
                finally:
                    if conn:
                        conn.close()
                st.rerun()
    else:
        st.info("Nenhuma conta contábil específica por banco cadastrada para esta empresa.")

    st.subheader("Adicionar/Editar Conta por Banco")
    with st.form("form_conta_banco"):
        current_nome_banco = ""
        current_conta_contabil = ""
        if st.session_state.editando_conta_banco_id:
            conta_para_editar = df_contas_banco[df_contas_banco['id'] == st.session_state.editando_conta_banco_id].iloc[0]
            current_nome_banco = conta_para_editar['nome_banco']
            current_conta_contabil = conta_para_editar['conta_contabil']
            
        banco_options = sorted(list(BANCOS_KEYWORDS.keys()))
        banco_idx = banco_options.index(current_nome_banco) if current_nome_banco in banco_options else 0
        
        novo_nome_banco = st.selectbox("Nome do Banco", banco_options, index=banco_idx, key="novo_nome_banco")
        nova_conta_contabil = st.text_input("Conta Contábil", value=current_conta_contabil, key="nova_conta_contabil")
        
        col_cb1, col_cb2 = st.columns(2)
        if col_cb1.form_submit_button("Salvar Conta"):
            if novo_nome_banco and nova_conta_contabil:
                conn = None
                try:
                    conn = get_connection()
                    cursor = conn.cursor()
                    if st.session_state.editando_conta_banco_id:
                        # Salva o estado anterior para o undo
                        old_data = df_contas_banco[df_contas_banco['id'] == st.session_state.editando_conta_banco_id].iloc[0]
                        undo_manager.push('editar_conta_banco', {
                            'id_conta': st.session_state.editando_conta_banco_id,
                            'old_nome_banco': old_data['nome_banco'],
                            'old_conta_contabil': old_data['conta_contabil']
                        })
                        query = "UPDATE empresa_banco_contas SET nome_banco = %s, conta_contabil = %s WHERE id = %s"
                        cursor.execute(query, (novo_nome_banco, nova_conta_contabil, st.session_state.editando_conta_banco_id))
                        st.success("Conta por banco atualizada!")
                    else:
                        query = "INSERT INTO empresa_banco_contas (id_empresa, nome_banco, conta_contabil) VALUES (%s, %s, %s)"
                        cursor.execute(query, (id_empresa, novo_nome_banco, nova_conta_contabil))
                        id_inserido = cursor.lastrowid
                        undo_manager.push('adicionar_conta_banco', {'id_conta': id_inserido})
                        st.success("Conta por banco adicionada!")
                    conn.commit()
                    st.session_state.editando_conta_banco_id = None
                except mysql.connector.Error as err:
                    logging.error(f"Erro ao salvar conta por banco: {err}")
                    st.error(f"Erro ao salvar conta por banco: {err}")
                finally:
                    if conn:
                        conn.close()
                st.rerun()
            else:
                st.error("Preencha todos os campos para salvar a conta.")
        if col_cb2.form_submit_button("Cancelar"):
            st.session_state.editando_conta_banco_id = None
            st.rerun()
