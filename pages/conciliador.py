import re
import unicodedata
import io
import pdfplumber
import pandas as pd
import mysql.connector
from thefuzz import fuzz
import streamlit as st

# --- Configuração da Conexão (assumindo st.secrets já configurado) ---
def get_connection():
    return mysql.connector.connect(
        host=st.secrets["mysql"]["host"],
        user=st.secrets["mysql"]["user"],
        password=st.secrets["mysql"]["password"],
        database=st.secrets["mysql"]["database"],
        use_pure=True,
        ssl_disabled=True
    )

# --- Funções de Utilitário ---
def padronizar_texto(texto):
    if not texto: return ""
    texto_sem_acento = unicodedata.normalize('NFKD', str(texto)).encode('ASCII', 'ignore').decode('utf-8')
    texto_limpo = re.sub(r'\s+', ' ', texto_sem_acento.upper().strip())
    return texto_limpo

def limpar_cnpj(cnpj):
    """Remove caracteres não numéricos de um CNPJ."""
    if not cnpj: return ""
    return re.sub(r'[^0-9]', '', str(cnpj))

def formatar_cnpj(cnpj_limpo):
    """Formata um CNPJ limpo (apenas números) para o padrão XX.XXX.XXX/XXXX-XX."""
    if not cnpj_limpo or len(cnpj_limpo) != 14:
        return cnpj_limpo  # Retorna como está se não for um CNPJ válido
    return f"{cnpj_limpo[:2]}.{cnpj_limpo[2:5]}.{cnpj_limpo[5:8]}/{cnpj_limpo[8:12]}-{cnpj_limpo[12:]}"

# --- Funções de Busca no Banco de Dados (Otimizadas com Prepared Statements) ---

def buscar_empresa_por_cnpj_otimizado(cnpj_limpo, conexao):
    """
    Busca uma empresa pelo CNPJ limpo no banco de dados.
    Retorna um dicionário com os dados da empresa ou None se não encontrada.
    """
    cursor = conexao.cursor(dictionary=True)
    query = (
        "SELECT id, nome, cnpj, tipo, apelido_unidade, conta_contabil "
        "FROM empresas "
        "WHERE cnpj = %s "
        "OR REPLACE(REPLACE(REPLACE(cnpj, '.', ''), '/', ''), '-', '') = %s"
    )
    try:
        cursor.execute(query, (formatar_cnpj(cnpj_limpo), cnpj_limpo))
        empresa = cursor.fetchone()
        return empresa
    except mysql.connector.Error as err:
        st.error(f"Erro ao buscar empresa por CNPJ: {err}")
        return None
    finally:
        cursor.close()

def obter_contas_empresa(id_empresa, conexao):
    """
    Retorna todos os mapeamentos de contas bancárias para uma empresa específica.
    Retorna um DataFrame.
    """
    query = "SELECT id, nome_banco, conta_contabil FROM empresa_banco_contas WHERE id_empresa = %s"
    try:
        df_contas = pd.read_sql(query, conexao, params=(id_empresa,))
        return df_contas
    except mysql.connector.Error as err:
        st.error(f"Erro ao obter contas da empresa: {err}")
        return pd.DataFrame()

def buscar_conta_por_banco(id_empresa, nome_banco, conexao):
    """
    Busca a conta contábil específica de uma empresa para um determinado banco.
    Retorna a string da conta contábil ou None.
    """
    cursor = conexao.cursor()
    query = "SELECT conta_contabil FROM empresa_banco_contas WHERE id_empresa = %s AND nome_banco = %s"
    try:
        cursor.execute(query, (id_empresa, nome_banco.upper()))  # Padroniza nome_banco para UPPER
        resultado = cursor.fetchone()
        return resultado[0] if resultado else None
    except mysql.connector.Error as err:
        st.error(f"Erro ao buscar conta por banco: {err}")
        return None
    finally:
        cursor.close()

# --- Funções de Detecção e Formatação ---

def identificar_banco_no_pdf(file_bytes):
    """
    Tenta identificar o nome do banco no cabeçalho do PDF.
    Retorna o nome padronizado do banco (ex: 'STONE', 'SICOOB') ou None.
    """
    BANCOS_KEYWORDS = {
        'STONE': ['STONE', 'INSTITUIÇÃO DE PAGAMENTO'],
        'SICOOB': ['SICOOB', 'BANCOOB'],
        'BRADESCO': ['BRADESCO', 'BANCO BRADESCO'],
        'ITAU': ['ITAU', 'BANCO ITAU'],
        'SANTANDER': ['SANTANDER', 'BANCO SANTANDER'],
        'CAIXA': ['CAIXA', 'CAIXA ECONOMICA FEDERAL'],
        # Adicione mais bancos e suas keywords conforme necessário
    }

    try:
        with pdfplumber.open(io.BytesIO(file_bytes)) as pdf:
            if len(pdf.pages) > 0:
                # Extrai texto da primeira página, focando no topo (cabeçalho)
                # Ajuste o bbox conforme a localização exata do cabeçalho no seu PDF
                # Ex: (x0, y0, x1, y1) - aqui, pegando os primeiros 200 pixels de altura
                primeira_pagina_texto = pdf.pages[0].extract_text(
                    # bbox=(0, 0, pdf.pages[0].width, 200)  # Exemplo de bbox para o topo
                )
                if not primeira_pagina_texto:
                    return None

                texto_upper = primeira_pagina_texto.upper()

                for banco, keywords in BANCOS_KEYWORDS.items():
                    for keyword in keywords:
                        if keyword.upper() in texto_upper:
                            return banco  # Retorna o nome padronizado do banco
    except Exception as e:
        st.warning(f"Não foi possível identificar o banco no PDF: {e}")
    return None

def formatar_contraparte_display(empresa_data):
    """
    Formata os dados da empresa para exibição no campo 'Contraparte'.
    """
    if not empresa_data:
        return ""
    nome = empresa_data.get('nome', 'N/A')
    tipo = empresa_data.get('tipo', 'N/A')
    cnpj = empresa_data.get('cnpj', 'N/A')
    return f"{nome} | {tipo} | {cnpj}"

# --- Variáveis de Sessão ---
if 'skipped_indices' not in st.session_state:
    st.session_state.skipped_indices = []
if 'editando_regra_id' not in st.session_state:
    st.session_state.editando_regra_id = None
if 'historico_acoes' not in st.session_state:
    st.session_state.historico_acoes = []
if 'empresa_detectada_id' not in st.session_state:
    st.session_state.empresa_detectada_id = None
if 'banco_detectado' not in st.session_state:
    st.session_state.banco_detectado = None
if 'conta_contabil_detectada' not in st.session_state:
    st.session_state.conta_contabil_detectada = None

# --- 2º PASSO: INTELIGÊNCIA DE PRÉ-SELEÇÃO ---
indice_sugerido = 0
st.session_state.empresa_detectada_id = None
st.session_state.banco_detectado = None
st.session_state.conta_contabil_detectada = None

if uploaded_files:
    for file in uploaded_files:
        file_bytes = file.getvalue()

        # 1. Tentar identificar o banco no PDF
        banco_detectado = identificar_banco_no_pdf(file_bytes)
        if banco_detectado:
            st.session_state.banco_detectado = banco_detectado
            st.toast(f"✅ Banco '{banco_detectado}' reconhecido no extrato!")

        # 2. Tentar identificar a empresa pelo CNPJ no PDF
        try:
            with pdfplumber.open(io.BytesIO(file_bytes)) as pdf:
                if len(pdf.pages) > 0:
                    texto_cabecalho = pdf.pages[0].extract_text(
                        # bbox=(0, 0, pdf.pages[0].width, 200)  # Exemplo de bbox para o topo
                    )
                    if texto_cabecalho:
                        match_cnpj = re.search(r'\d{2}\.?\d{3}\.?\d{3}/?\d{4}-?\d{2}', texto_cabecalho)
                        if match_cnpj:
                            cnpj_lido_limpo = limpar_cnpj(match_cnpj.group(0))

                            conn = get_connection()
                            empresa_encontrada = buscar_empresa_por_cnpj_otimizado(cnpj_lido_limpo, conn)
                            conn.close()

                            if empresa_encontrada:
                                st.session_state.empresa_detectada_id = empresa_encontrada['id']
                                # Encontrar o índice para o selectbox
                                df_empresas['cnpj_limpo'] = df_empresas['cnpj'].astype(str).apply(lambda x: limpar_cnpj(x))
                                match_df = df_empresas[df_empresas['cnpj_limpo'] == cnpj_lido_limpo]
                                if not match_df.empty:
                                    indice_sugerido = df_empresas.index.get_loc(match_df.index[0])
                                st.toast(f"✅ Extrato da empresa {empresa_encontrada['nome']} reconhecido!")
                                break  # Processa apenas o primeiro arquivo para detecção inicial
        except Exception as e:
            st.warning(f"Erro na detecção de CNPJ no PDF: {e}")
            pass  # Continua sem detecção automática de empresa

# --- 3º PASSO: PAINEL DE CONFIGURAÇÕES ---
st.markdown("### 2. Confirme os Dados")
col_cfg1, col_cfg2, col_cfg3 = st.columns([2, 1, 1])

# Selectbox de Empresa
empresa_sel_display = col_cfg1.selectbox(
    "Empresa / Filial",
    df_empresas['display_nome'],
    index=indice_sugerido  # Usa o índice sugerido pela detecção
)
empresa_data = df_empresas[df_empresas['display_nome'] == empresa_sel_display].iloc[0]
id_empresa = int(empresa_data['id'])

# Dropdown para seleção do Banco (se detectado, já vem preenchido)
conn = get_connection()
df_contas_empresa = obter_contas_empresa(id_empresa, conn)
conn.close()

bancos_disponiveis = df_contas_empresa['nome_banco'].tolist()
if not bancos_disponiveis:
    bancos_disponiveis = ["Nenhum banco cadastrado"]  # Fallback
    st.warning(f"Nenhum mapeamento de conta bancária encontrado para a empresa {empresa_data['nome']}. Cadastre em 'Gerenciar Contas por Banco'.")

# Tentar pré-selecionar o banco detectado
idx_banco_sugerido = 0
if st.session_state.banco_detectado and st.session_state.banco_detectado in bancos_disponiveis:
    idx_banco_sugerido = bancos_disponiveis.index(st.session_state.banco_detectado)
elif "Nenhum banco cadastrado" in bancos_disponiveis:
    idx_banco_sugerido = 0
elif bancos_disponiveis:
    idx_banco_sugerido = 0

banco_selecionado = col_cfg2.selectbox(
    "Banco do Extrato",
    bancos_disponiveis,
    index=idx_banco_sugerido
)

# Buscar a conta contábil com base na empresa e banco selecionados
conta_banco_fixa = ""
if banco_selecionado and banco_selecionado != "Nenhum banco cadastrado":
    conn = get_connection()
    conta_banco_fixa = buscar_conta_por_banco(id_empresa, banco_selecionado, conn)
    conn.close()
    if not conta_banco_fixa:
        st.warning(f"Conta contábil não encontrada para {empresa_data['nome']} no banco {banco_selecionado}. Verifique o cadastro.")
        conta_banco_fixa = "000"  # Fallback para evitar erro

col_cfg2.text_input("Conta Banco (Âncora)", value=conta_banco_fixa, disabled=True)  # Campo desabilitado

saldo_anterior_informado = col_cfg3.number_input("Saldo Anterior (R$)", value=0.00, step=100.00, format="%.2f")

# --- Lógica para preencher CONTRAPARTE ---
contraparte_display = formatar_contraparte_display(empresa_data)

# --- Dentro do loop de classificação ---
# ...
#             if not match:
#                 contraparte_sugerida = ""
#                 if st.session_state.empresa_detectada_id == id_empresa:
#                     contraparte_sugerida = formatar_contraparte_display(empresa_data)
#
#                 pendentes.append({
#                     'idx_original': idx,
#                     'Data': row['Data'],
#                     'Descricao': row['Descricao'],
#                     'Valor': row['Valor'],
#                     'Sinal': row['Sinal'],
#                     'Contraparte_Sugerida': contraparte_sugerida
#                 })
# ...

# --- GERENCIAMENTO DE REGRAS ---
st.divider()
with st.expander("📚 Gerenciar Regras e Contas Cadastradas", expanded=False):

    # --- Subseção: Gerenciar Regras (existente) ---
    st.subheader("Gerenciar Regras de Classificação")
    # ... (código existente para gerenciar regras) ...

    st.markdown("---")  # Separador visual

    # --- Subseção: Gerenciar Contas por Banco ---
    st.subheader("📊 Gerenciar Contas por Banco")

    conn = get_connection()
    df_contas_gerenciamento = obter_contas_empresa(id_empresa, conn)
    conn.close()

    # Tabela de exibição
    if not df_contas_gerenciamento.empty:
        st.dataframe(
            df_contas_gerenciamento[['nome_banco', 'conta_contabil']].rename(
                columns={'nome_banco': 'Banco', 'conta_contabil': 'Conta Contábil'}
            ),
            use_container_width=True,
            hide_index=True
        )
    else:
        st.info(f"Nenhum mapeamento de conta bancária encontrado para a empresa {empresa_data['nome']}.")

    # Formulário para Adicionar/Editar
    with st.form("form_gerenciar_contas"):
        st.markdown("##### Adicionar/Editar Mapeamento de Conta")
        col_add1, col_add2 = st.columns(2)

        st.write(f"**Empresa:** {empresa_data['nome']}")

        banco_input = col_add1.text_input(
            "Nome do Banco (Padronizado, ex: STONE, SICOOB)",
            key="banco_input_add_edit"
        )
        conta_contabil_input = col_add2.text_input(
            "Conta Contábil",
            key="conta_contabil_input_add_edit"
        )

        submitted = st.form_submit_button("➕ Salvar Mapeamento")

        if submitted:
            if not banco_input or not conta_contabil_input:
                st.error("Por favor, preencha o nome do banco e a conta contábil.")
            else:
                conn = get_connection()
                cursor = conn.cursor()
                try:
                    query = """
                    INSERT INTO empresa_banco_contas (id_empresa, nome_banco, conta_contabil)
                    VALUES (%s, %s, %s)
                    ON DUPLICATE KEY UPDATE conta_contabil = VALUES(conta_contabil)
                    """
                    cursor.execute(query, (id_empresa, banco_input.upper(), conta_contabil_input))
                    conn.commit()
                    st.success(f"Mapeamento para {banco_input.upper()} salvo com sucesso!")
                    st.rerun()
                except mysql.connector.Error as err:
                    st.error(f"Erro ao salvar mapeamento: {err}")
                finally:
                    cursor.close()
                    conn.close()

        st.markdown("---")

    # Botões de Edição/Remoção para cada linha da tabela
    if not df_contas_gerenciamento.empty:
        st.markdown("##### Ações para Mapeamentos Existentes")
        for idx, row in df_contas_gerenciamento.iterrows():
            col_r = st.columns([3, 2, 1, 1])
            col_r[0].write(f"**Banco:** {row['nome_banco']}")
            col_r[1].write(f"**Conta:** {row['conta_contabil']}")

            # Botão de Edição
            if col_r[2].button("✏️", key=f"edit_conta_{row['id']}"):
                st.session_state.editando_conta_id = row['id']
                st.session_state.banco_input_add_edit = row['nome_banco']
                st.session_state.conta_contabil_input_add_edit = row['conta_contabil']
                st.rerun()

            # Botão de Remoção
            if col_r[3].button("🗑️", key=f"delete_conta_{row['id']}"):
                conn = get_connection()
                cursor = conn.cursor()
                try:
                    cursor.execute("DELETE FROM empresa_banco_contas WHERE id = %s", (row['id'],))
                    conn.commit()
                    st.success(f"Mapeamento para {row['nome_banco']} removido.")
                    st.rerun()
                except mysql.connector.Error as err:
                    st.error(f"Erro ao remover mapeamento: {err}")
                finally:
                    cursor.close()
                    conn.close()
