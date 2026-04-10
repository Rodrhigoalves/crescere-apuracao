import re
import unicodedata
import io
import pdfplumber
import pandas as pd
import mysql.connector
from thefuzz import fuzz
import streamlit as st

# =============================================================================
# CONFIGURAÇÃO DA CONEXÃO
# =============================================================================
def get_connection():
    return mysql.connector.connect(
        host=st.secrets["mysql"]["host"],
        user=st.secrets["mysql"]["user"],
        password=st.secrets["mysql"]["password"],
        database=st.secrets["mysql"]["database"],
        use_pure=True,
        ssl_disabled=True
    )

# =============================================================================
# FUNÇÕES DE UTILITÁRIO
# =============================================================================
def padronizar_texto(texto):
    if not texto:
        return ""
    texto_sem_acento = unicodedata.normalize('NFKD', str(texto)).encode('ASCII', 'ignore').decode('utf-8')
    return re.sub(r'\s+', ' ', texto_sem_acento.upper().strip())

def limpar_cnpj(cnpj):
    """Remove caracteres não numéricos de um CNPJ."""
    if not cnpj:
        return ""
    return re.sub(r'[^0-9]', '', str(cnpj))

def formatar_cnpj(cnpj_limpo):
    """Formata um CNPJ limpo (apenas números) para o padrão XX.XXX.XXX/XXXX-XX."""
    if not cnpj_limpo or len(cnpj_limpo) != 14:
        return cnpj_limpo
    return f"{cnpj_limpo[:2]}.{cnpj_limpo[2:5]}.{cnpj_limpo[5:8]}/{cnpj_limpo[8:12]}-{cnpj_limpo[12:]}"

# =============================================================================
# FUNÇÕES DE BANCO DE DADOS
# =============================================================================
def carregar_empresas(conexao):
    """Carrega todas as empresas do banco e retorna um DataFrame."""
    query = "SELECT id, nome, cnpj, tipo, apelido_unidade, conta_contabil FROM empresas ORDER BY nome"
    try:
        df = pd.read_sql(query, conexao)
        df['display_nome'] = df['nome'] + " (" + df['cnpj'].fillna('') + ")"
        return df
    except mysql.connector.Error as err:
        st.error(f"Erro ao carregar empresas: {err}")
        return pd.DataFrame()

def buscar_empresa_por_cnpj_otimizado(cnpj_limpo, conexao):
    """Busca uma empresa pelo CNPJ limpo. Retorna dict ou None."""
    cursor = conexao.cursor(dictionary=True)
    query = (
        "SELECT id, nome, cnpj, tipo, apelido_unidade, conta_contabil "
        "FROM empresas "
        "WHERE cnpj = %s "
        "OR REPLACE(REPLACE(REPLACE(cnpj, '.', ''), '/', ''), '-', '') = %s"
    )
    try:
        cursor.execute(query, (formatar_cnpj(cnpj_limpo), cnpj_limpo))
        return cursor.fetchone()
    except mysql.connector.Error as err:
        st.error(f"Erro ao buscar empresa por CNPJ: {err}")
        return None
    finally:
        cursor.close()

def obter_contas_empresa(id_empresa, conexao):
    """Retorna DataFrame com mapeamentos de contas bancárias de uma empresa."""
    query = "SELECT id, nome_banco, conta_contabil FROM empresa_banco_contas WHERE id_empresa = %s"
    try:
        return pd.read_sql(query, conexao, params=(id_empresa,))
    except mysql.connector.Error as err:
        st.error(f"Erro ao obter contas da empresa: {err}")
        return pd.DataFrame()

def buscar_conta_por_banco(id_empresa, nome_banco, conexao):
    """Retorna a conta contábil de uma empresa para um banco específico, ou None."""
    cursor = conexao.cursor()
    query = "SELECT conta_contabil FROM empresa_banco_contas WHERE id_empresa = %s AND nome_banco = %s"
    try:
        cursor.execute(query, (id_empresa, nome_banco.upper()))
        resultado = cursor.fetchone()
        return resultado[0] if resultado else None
    except mysql.connector.Error as err:
        st.error(f"Erro ao buscar conta por banco: {err}")
        return None
    finally:
        cursor.close()

# =============================================================================
# FUNÇÕES DE DETECÇÃO E FORMATAÇÃO
# =============================================================================
def identificar_banco_no_pdf(file_bytes):
    """Identifica o banco no cabeçalho do PDF. Retorna nome padronizado ou None."""
    BANCOS_KEYWORDS = {
        'STONE':     ['STONE', 'INSTITUIÇÃO DE PAGAMENTO'],
        'SICOOB':    ['SICOOB', 'BANCOOB'],
        'BRADESCO':  ['BRADESCO', 'BANCO BRADESCO'],
        'ITAU':      ['ITAU', 'BANCO ITAU'],
        'SANTANDER': ['SANTANDER', 'BANCO SANTANDER'],
        'CAIXA':     ['CAIXA', 'CAIXA ECONOMICA FEDERAL'],
    }
    try:
        with pdfplumber.open(io.BytesIO(file_bytes)) as pdf:
            if not pdf.pages:
                return None
            texto = pdf.pages[0].extract_text()
            if not texto:
                return None
            texto_upper = texto.upper()
            for banco, keywords in BANCOS_KEYWORDS.items():
                for kw in keywords:
                    if kw.upper() in texto_upper:
                        return banco
    except Exception as e:
        st.warning(f"Não foi possível identificar o banco no PDF: {e}")
    return None

def formatar_contraparte_display(empresa_data):
    """Formata dados da empresa para exibição no campo Contraparte."""
    if empresa_data is None:
        return ""
    # Suporta tanto dict (resultado de query direta) quanto Series (linha de DataFrame)
    if isinstance(empresa_data, pd.Series):
        nome = empresa_data.get('nome', 'N/A')
        tipo = empresa_data.get('tipo', 'N/A')
        cnpj = empresa_data.get('cnpj', 'N/A')
    else:
        nome = empresa_data.get('nome', 'N/A')
        tipo = empresa_data.get('tipo', 'N/A')
        cnpj = empresa_data.get('cnpj', 'N/A')
    return f"{nome} | {tipo} | {cnpj}"

# =============================================================================
# INICIALIZAÇÃO DO ESTADO DE SESSÃO
# =============================================================================
defaults = {
    'skipped_indices': [],
    'editando_regra_id': None,
    'historico_acoes': [],
    'empresa_detectada_id': None,
    'banco_detectado': None,
    'conta_contabil_detectada': None,
    'editando_conta_id': None,
}
for key, val in defaults.items():
    if key not in st.session_state:
        st.session_state[key] = val

# =============================================================================
# CABEÇALHO DA PÁGINA
# =============================================================================
st.title("🏦 Conciliador de Extratos")
st.markdown("---")

# =============================================================================
# 1º PASSO: CARREGAMENTO DE EMPRESAS
# =============================================================================
conn = get_connection()
df_empresas = carregar_empresas(conn)
conn.close()

if df_empresas.empty:
    st.error("Nenhuma empresa encontrada no banco de dados. Verifique a conexão e a tabela 'empresas'.")
    st.stop()

# =============================================================================
# 2º PASSO: UPLOAD DO ARQUIVO
# =============================================================================
st.markdown("### 1. Envie o Extrato")
uploaded_files = st.file_uploader(
    "Selecione o(s) extrato(s) em PDF",
    type=["pdf"],
    accept_multiple_files=True,
    key="uploader_extratos"
)

# =============================================================================
# 3º PASSO: INTELIGÊNCIA DE PRÉ-SELEÇÃO (executa após upload)
# =============================================================================
indice_sugerido = 0
st.session_state.empresa_detectada_id = None
st.session_state.banco_detectado = None
st.session_state.conta_contabil_detectada = None

if uploaded_files:
    for file in uploaded_files:
        file_bytes = file.getvalue()

        # Detectar banco
        banco_detectado = identificar_banco_no_pdf(file_bytes)
        if banco_detectado:
            st.session_state.banco_detectado = banco_detectado
            st.toast(f"✅ Banco '{banco_detectado}' reconhecido no extrato!")

        # Detectar empresa pelo CNPJ
        try:
            with pdfplumber.open(io.BytesIO(file_bytes)) as pdf:
                if pdf.pages:
                    texto_cabecalho = pdf.pages[0].extract_text()
                    if texto_cabecalho:
                        match_cnpj = re.search(r'\d{2}\.?\d{3}\.?\d{3}/?\d{4}-?\d{2}', texto_cabecalho)
                        if match_cnpj:
                            cnpj_lido_limpo = limpar_cnpj(match_cnpj.group(0))

                            conn = get_connection()
                            empresa_encontrada = buscar_empresa_por_cnpj_otimizado(cnpj_lido_limpo, conn)
                            conn.close()

                            if empresa_encontrada:
                                st.session_state.empresa_detectada_id = empresa_encontrada['id']
                                df_empresas['cnpj_limpo'] = df_empresas['cnpj'].astype(str).apply(limpar_cnpj)
                                match_df = df_empresas[df_empresas['cnpj_limpo'] == cnpj_lido_limpo]
                                if not match_df.empty:
                                    indice_sugerido = df_empresas.index.get_loc(match_df.index[0])
                                st.toast(f"✅ Empresa '{empresa_encontrada['nome']}' reconhecida!")
                                break  # Usa apenas o primeiro arquivo para detecção
        except Exception as e:
            st.warning(f"Erro na detecção de CNPJ no PDF: {e}")

# =============================================================================
# 4º PASSO: PAINEL DE CONFIGURAÇÕES
# =============================================================================
st.markdown("### 2. Confirme os Dados")
col_cfg1, col_cfg2, col_cfg3 = st.columns([2, 1, 1])

# Selectbox de Empresa
empresa_sel_display = col_cfg1.selectbox(
    "Empresa / Filial",
    df_empresas['display_nome'],
    index=indice_sugerido
)
empresa_data = df_empresas[df_empresas['display_nome'] == empresa_sel_display].iloc[0]
id_empresa = int(empresa_data['id'])

# Carregar bancos da empresa selecionada
conn = get_connection()
df_contas_empresa = obter_contas_empresa(id_empresa, conn)
conn.close()

bancos_disponiveis = df_contas_empresa['nome_banco'].tolist() if not df_contas_empresa.empty else []

if not bancos_disponiveis:
    bancos_disponiveis = ["Nenhum banco cadastrado"]
    col_cfg2.warning(f"Nenhum banco cadastrado para '{empresa_data['nome']}'.")

# Pré-selecionar banco detectado automaticamente
idx_banco_sugerido = 0
if st.session_state.banco_detectado and st.session_state.banco_detectado in bancos_disponiveis:
    idx_banco_sugerido = bancos_disponiveis.index(st.session_state.banco_detectado)

banco_selecionado = col_cfg2.selectbox(
    "Banco do Extrato",
    bancos_disponiveis,
    index=idx_banco_sugerido
)

# Buscar conta contábil para empresa + banco selecionados
conta_banco_fixa = ""
if banco_selecionado and banco_selecionado != "Nenhum banco cadastrado":
    conn = get_connection()
    conta_banco_fixa = buscar_conta_por_banco(id_empresa, banco_selecionado, conn)
    conn.close()
    if not conta_banco_fixa:
        col_cfg2.warning(f"Conta contábil não encontrada para {banco_selecionado}. Verifique o cadastro.")
        conta_banco_fixa = "000"

col_cfg2.text_input("Conta Banco (Âncora)", value=conta_banco_fixa, disabled=True)
saldo_anterior_informado = col_cfg3.number_input("Saldo Anterior (R$)", value=0.00, step=100.00, format="%.2f")

# Contraparte formatada para uso nos lançamentos
contraparte_display = formatar_contraparte_display(empresa_data)

# =============================================================================
# 5º PASSO: GERENCIAMENTO DE REGRAS E CONTAS
# =============================================================================
st.divider()
with st.expander("📚 Gerenciar Regras e Contas Cadastradas", expanded=False):

    st.subheader("Gerenciar Regras de Classificação")
    # ... (insira aqui o código existente de gerenciamento de regras) ...

    st.markdown("---")

    st.subheader("📊 Gerenciar Contas por Banco")

    conn = get_connection()
    df_contas_gerenciamento = obter_contas_empresa(id_empresa, conn)
    conn.close()

    if not df_contas_gerenciamento.empty:
        st.dataframe(
            df_contas_gerenciamento[['nome_banco', 'conta_contabil']].rename(
                columns={'nome_banco': 'Banco', 'conta_contabil': 'Conta Contábil'}
            ),
            use_container_width=True,
            hide_index=True
        )
    else:
        st.info(f"Nenhum mapeamento encontrado para '{empresa_data['nome']}'.")

    # Formulário para adicionar/editar mapeamento
    with st.form("form_gerenciar_contas"):
        st.markdown("##### Adicionar / Editar Mapeamento")
        col_add1, col_add2 = st.columns(2)
        st.write(f"**Empresa:** {empresa_data['nome']}")

        banco_input = col_add1.text_input(
            "Nome do Banco (ex: STONE, SICOOB)",
            key="banco_input_add_edit"
        )
        conta_contabil_input = col_add2.text_input(
            "Conta Contábil",
            key="conta_contabil_input_add_edit"
        )

        if st.form_submit_button("➕ Salvar Mapeamento"):
            if not banco_input or not conta_contabil_input:
                st.error("Preencha o nome do banco e a conta contábil.")
            else:
                conn = get_connection()
                cursor = conn.cursor()
                try:
                    cursor.execute(
                        """
                        INSERT INTO empresa_banco_contas (id_empresa, nome_banco, conta_contabil)
                        VALUES (%s, %s, %s)
                        ON DUPLICATE KEY UPDATE conta_contabil = VALUES(conta_contabil)
                        """,
                        (id_empresa, banco_input.upper(), conta_contabil_input)
                    )
                    conn.commit()
                    st.success(f"Mapeamento para '{banco_input.upper()}' salvo!")
                    st.rerun()
                except mysql.connector.Error as err:
                    st.error(f"Erro ao salvar mapeamento: {err}")
                finally:
                    cursor.close()
                    conn.close()

    # Ações por linha existente
    if not df_contas_gerenciamento.empty:
        st.markdown("##### Ações para Mapeamentos Existentes")
        for _, row in df_contas_gerenciamento.iterrows():
            col_r = st.columns([3, 2, 1, 1])
            col_r[0].write(f"**Banco:** {row['nome_banco']}")
            col_r[1].write(f"**Conta:** {row['conta_contabil']}")

            if col_r[2].button("✏️", key=f"edit_conta_{row['id']}"):
                st.session_state.editando_conta_id = row['id']
                st.session_state.banco_input_add_edit = row['nome_banco']
                st.session_state.conta_contabil_input_add_edit = row['conta_contabil']
                st.rerun()

            if col_r[3].button("🗑️", key=f"delete_conta_{row['id']}"):
                conn = get_connection()
                cursor = conn.cursor()
                try:
                    cursor.execute("DELETE FROM empresa_banco_contas WHERE id = %s", (row['id'],))
                    conn.commit()
                    st.success(f"Mapeamento para '{row['nome_banco']}' removido.")
                    st.rerun()
                except mysql.connector.Error as err:
                    st.error(f"Erro ao remover mapeamento: {err}")
                finally:
                    cursor.close()
                    conn.close()
