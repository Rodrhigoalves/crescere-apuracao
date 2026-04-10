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

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

# =============================================================================
# CLASSE UNDO STACK
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
# 1. UTILITÁRIOS E CONEXÃO
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


def formatar_contraparte_display(empresa_data):
    if empresa_data is None:
        return "Empresa Não Identificada"
    cnpj_formatado = formatar_cnpj(limpar_cnpj(empresa_data['cnpj']))
    return f"{empresa_data['nome']} | {empresa_data['tipo']} | {cnpj_formatado}"


# =============================================================================
# 2. INTELIGÊNCIA: AUTO-LEITURA DE CNPJ E BANCO NO CABEÇALHO
# =============================================================================
BBOX_HEADER_AREA    = (0, 0, 600, 150)
BBOX_BANK_NAME_AREA = (50, 0, 550, 150)

BANCOS_KEYWORDS = {
    'STONE':     ['STONE', 'INSTITUIÇÃO DE PAGAMENTO'],
    'SICOOB':    ['SICOOB', 'BANCOOB', 'SICOOB BANCO'],
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
                if not header_text:
                    return None
                cnpj_match = re.search(r'\d{2}\.?\d{3}\.?\d{3}/?\d{4}-?\d{2}', header_text)
                if cnpj_match:
                    return cnpj_match.group(0)
    except Exception as e:
        logging.error(f"Erro ao identificar CNPJ no PDF: {e}")
    return None


def identificar_banco_no_pdf(file_bytes):
    try:
        with pdfplumber.open(io.BytesIO(file_bytes)) as pdf:
            if len(pdf.pages) > 0:
                header_text = pdf.pages[0].crop(BBOX_BANK_NAME_AREA).extract_text()
                if not header_text:
                    return "DESCONHECIDO"
                header_upper = padronizar_texto(header_text)
                for banco, keywords in BANCOS_KEYWORDS.items():
                    for kw in keywords:
                        if padronizar_texto(kw) in header_upper:
                            return banco
    except Exception as e:
        logging.error(f"Erro ao identificar banco no PDF: {e}")
    return "DESCONHECIDO"


@st.cache_data(show_spinner=False)
def buscar_empresa_por_cnpj_otimizado(cnpj_formatado, df_empresas):
    if not cnpj_formatado:
        return None
    cnpj_limpo_buscado = limpar_cnpj(cnpj_formatado)
    if 'cnpj_limpo' not in df_empresas.columns:
        df_empresas = df_empresas.copy()
        df_empresas['cnpj_limpo'] = df_empresas['cnpj'].astype(str).apply(limpar_cnpj)
    match_df = df_empresas[df_empresas['cnpj_limpo'] == cnpj_limpo_buscado]
    if not match_df.empty:
        return match_df.iloc[0].to_dict()
    return None


@st.cache_data(ttl=60, show_spinner=False)
def buscar_conta_por_banco(id_empresa, nome_banco):
    conn = None
    try:
        conn = get_connection()
        cursor = conn.cursor(dictionary=True)
        cursor.execute(
            "SELECT conta_contabil FROM empresa_banco_contas WHERE id_empresa = %s AND nome_banco = %s",
            (id_empresa, nome_banco)
        )
        result = cursor.fetchone()
        return result['conta_contabil'] if result else None
    except mysql.connector.Error as err:
        logging.error(f"Erro ao buscar conta por banco: {err}")
        return None
    finally:
        if conn:
            conn.close()


# =============================================================================
# 3. MOTOR DE EXTRAÇÃO PDF E OFX
# =============================================================================
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
                if encontrou_y is not None:
                    linhas_dict[encontrou_y].append(p)
                else:
                    linhas_dict[y] = [p]
            for y_key in sorted(linhas_dict.keys()):
                linha_ordenada = sorted(linhas_dict[y_key], key=lambda x: x['x0'])
                texto_completo += " ".join([w['text'] for w in linha_ordenada]) + "\n"

    RUIDO_CABECALHO = [
        "período:", "página", "saldo anterior", "saldo atual", "saldo final",
        "cnpj", "emitido em", "extrato de conta", "dados da conta",
        "nome documento", "instituição agência", "contraparte stone"
    ]

    linhas = [l.strip() for l in texto_completo.split('\n') if l.strip()]
    dados, ignoradas_raw = [], []

    # Padrões de busca independentes da ordem na linha
    regex_data = r'\d{2}/\d{2}/\d{2,4}'
    regex_valor = r'-?\s*(?:R\$?\s*)?\d{1,3}(?:\.\d{3})*,\d{2}'

    for linha in linhas:
        if any(x in linha.lower() for x in RUIDO_CABECALHO):
            continue

        match_data = re.search(regex_data, linha)
        valores = re.findall(regex_valor, linha)

        # Se a linha tem uma data e pelo menos um valor monetário, é uma transação
        if match_data and valores:
            data = match_data.group(0)
            
            # O primeiro valor da linha costuma ser o da operação (o segundo é o saldo)
            valor_bruto = valores[0]
            
            # Verifica se é uma saída identificando sinal de menos ou um 'D' no final da linha
            is_negativo = '-' in valor_bruto or bool(re.search(r'\sD$', linha.strip(), re.IGNORECASE))
            is_positivo = '+' in valor_bruto or bool(re.search(r'\sC$', linha.strip(), re.IGNORECASE))
            
            # Limpa a formatação para converter para número
            valor_str_limpo = re.search(r'\d{1,3}(?:\.\d{3})*,\d{2}', valor_bruto).group(0)
            valor_num = float(valor_str_limpo.replace('.', '').replace(',', '.'))
            
            # A MÁGICA: Removemos a data e todos os valores monetários da linha. 
            # O que sobrar é a descrição completa (Tipo + Descrição real).
            desc_limpa = linha.replace(data, '')
            for v in valores:
                desc_limpa = desc_limpa.replace(v, '')
                
            # Removemos os sufixos D e C isolados que possam ter sobrado no texto
            desc_limpa = re.sub(r'\b[DC]\b', '', desc_limpa, flags=re.IGNORECASE)
            desc_limpa = padronizar_texto(desc_limpa.strip())
            
            if not desc_limpa or len(desc_limpa) < 2:
                desc_limpa = "SEM DESCRICAO"
                
            # Definição fina do sinal do lançamento
            desc_upper = desc_limpa.upper()
            if is_negativo:
                sinal = '-'
            elif is_positivo:
                sinal = '+'
            else:
                sinal = '+' if any(w in desc_upper for w in ['ENTRADA', 'DEPOSITO', 'DEPÓSITO', 'RECEBIMENTO', 'CREDITO', 'CRÉDITO', 'PIX RECEBIDO', 'RESGATE']) else '-'

            dados.append({
                'Data':      data,
                'Descricao': desc_limpa,
                'Valor':     abs(valor_num),
                'Sinal':     sinal
            })
        elif len(linha) > 8:
            # Linhas que não deram match vão para auditoria (possível lixo ou erro do PDF)
            ignoradas_raw.append(linha)

    ignoradas_unicas    = list(dict.fromkeys(ignoradas_raw))
    ignoradas_com_valor = [l for l in ignoradas_unicas if re.search(r'\d,\d{2}', l)]
    ignoradas_texto     = [l for l in ignoradas_unicas if not re.search(r'\d,\d{2}', l)]

    logging.info(f"Extração concluída. {len(dados)} transações, {len(ignoradas_raw)} linhas ignoradas.")
    return pd.DataFrame(dados), {"criticas": ignoradas_com_valor, "comuns": ignoradas_texto}


@st.cache_data(show_spinner=False)
def extrair_texto_ofx(file_bytes):
    dados_extraidos = []
    try:
        # 1. Tenta ler o arquivo no padrão moderno (UTF-8)
        try:
            texto_ofx = file_bytes.decode('utf-8')
        # 2. Se o banco mandou caracteres quebrados, força a leitura ignorando o erro
        except UnicodeDecodeError:
            texto_ofx = file_bytes.decode('latin-1', errors='ignore')

        # Transforma o texto corrigido em um arquivo na memória para o parser
        ofx = OfxParser.parse(io.StringIO(texto_ofx))
        
        for account in ofx.accounts:
            for tx in account.statement.transactions:
                valor = float(tx.amount)
                
                # Extrai os dados garantindo que não venham como None
                texto_memo = str(tx.memo).strip() if tx.memo else ""
                texto_payee = str(tx.payee).strip() if tx.payee else ""
                
                # Lógica de extração: Prioriza a DESCRIÇÃO (memo), mas aproveita 
                # o TIPO (payee) se ele contiver informações adicionais.
                if texto_memo and texto_payee and texto_payee not in texto_memo:
                    descricao_final = f"{texto_memo} {texto_payee}"
                elif texto_memo:
                    descricao_final = texto_memo
                else:
                    descricao_final = texto_payee
                    
                dados_extraidos.append({
                    'Data':     tx.date.strftime('%d/%m/%Y'),
                    'Descricao': padronizar_texto(descricao_final),
                    'Valor':    abs(valor),
                    'Sinal':    '+' if valor > 0 else '-'
                })
    except Exception as e:
        logging.error(f"Erro ao extrair OFX: {e}")
        st.error(f"Erro ao processar OFX: O arquivo contém formatação inválida. Detalhe: {e}")
    return pd.DataFrame(dados_extraidos)


# =============================================================================
# 4. CARGA DE DADOS DO BANCO
# =============================================================================
@st.cache_data(ttl=60, show_spinner=False)
def carregar_empresas():
    conn = None
    try:
        conn = get_connection()
        df = pd.read_sql(
            "SELECT id, nome, fantasia, cnpj, tipo, apelido_unidade, conta_contabil FROM empresas",
            conn
        )
        df['tipo']       = df['tipo'].fillna('Matriz')
        df['cnpj']       = df['cnpj'].fillna('Sem CNPJ')
        df['cnpj_limpo'] = df['cnpj'].astype(str).apply(limpar_cnpj)
        df['display_nome'] = df['nome'] + ' | ' + df['tipo'] + ' | ' + df['cnpj']
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
    conn = None
    try:
        conn = get_connection()
        return pd.read_sql(
            "SELECT id, nome_banco, conta_contabil FROM empresa_banco_contas WHERE id_empresa = %s ORDER BY nome_banco",
            conn,
            params=(id_empresa,)
        )
    except mysql.connector.Error as err:
        logging.error(f"Erro ao carregar contas por banco: {err}")
        return pd.DataFrame()
    finally:
        if conn:
            conn.close()


# =============================================================================
# 5. INTERFACE PRINCIPAL
# =============================================================================
st.set_page_config(page_title="Conciliação Bancária", page_icon="🏦", layout="wide")

# Variáveis de sessão
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
# 1º PASSO: UPLOAD
# =============================================================================
uploaded_files = st.file_uploader(
    "1. Arraste seus extratos (PDF ou OFX)",
    type=["pdf", "ofx"],
    accept_multiple_files=True
)

# =============================================================================
# 2º PASSO: PRÉ-SELEÇÃO AUTOMÁTICA
# =============================================================================
indice_sugerido = 0

if uploaded_files:
    for file in uploaded_files:
        if file.name.lower().endswith('.pdf'):
            cnpj_lido = identificar_cnpj_no_pdf(file.getvalue())
            if cnpj_lido:
                empresa_detectada_data = buscar_empresa_por_cnpj_otimizado(cnpj_lido, df_empresas)
                if empresa_detectada_data:
                    idx_encontrado = df_empresas[df_empresas['id'] == empresa_detectada_data['id']].index[0]
                    indice_sugerido = int(idx_encontrado)
                    st.toast(f"✅ Empresa '{empresa_detectada_data['nome']}' reconhecida pelo CNPJ!")
                    st.session_state.empresa_detectada_data = empresa_detectada_data
                else:
                    st.warning(f"CNPJ '{cnpj_lido}' encontrado no PDF, mas sem empresa correspondente no cadastro.")

            banco_detectado = identificar_banco_no_pdf(file.getvalue())
            if banco_detectado != "DESCONHECIDO":
                st.toast(f"✅ Banco '{banco_detectado}' identificado no PDF!")
                st.session_state.banco_detectado = banco_detectado
            break

# =============================================================================
# 3º PASSO: PAINEL DE CONFIGURAÇÕES
# =============================================================================
st.markdown("### 2. Confirme os Dados")
col_cfg1, col_cfg2, col_cfg3 = st.columns([2, 1, 1])

empresa_sel_display = col_cfg1.selectbox(
    "Empresa / Filial",
    df_empresas['display_nome'],
    index=indice_sugerido
)
empresa_data = df_empresas[df_empresas['display_nome'] == empresa_sel_display].iloc[0].to_dict()
id_empresa   = int(empresa_data['id'])

bancos_disponiveis = sorted(list(BANCOS_KEYWORDS.keys()) + [st.session_state.banco_detectado])
bancos_disponiveis = list(dict.fromkeys([b for b in bancos_disponiveis if b != "DESCONHECIDO"]))
banco_index = (
    bancos_disponiveis.index(st.session_state.banco_detectado)
    if st.session_state.banco_detectado in bancos_disponiveis else 0
)
banco_selecionado = col_cfg2.selectbox("Banco do Extrato", bancos_disponiveis, index=banco_index)

conta_banco_fixa = buscar_conta_por_banco(id_empresa, banco_selecionado)
if not conta_banco_fixa:
    conta_banco_fixa = empresa_data.get('conta_contabil', 'N/A')
    if conta_banco_fixa == 'N/A':
        st.warning(f"Nenhuma conta contábil definida para '{empresa_data['nome']}' / '{banco_selecionado}'.")

col_cfg2.text_input("Conta Banco (Âncora)", value=conta_banco_fixa, disabled=True)
saldo_anterior_informado = col_cfg3.number_input("Saldo Anterior (R$)", value=0.00, step=100.00, format="%.2f")

# =============================================================================
# 4º PASSO: PROCESSAMENTO
# =============================================================================
if uploaded_files and conta_banco_fixa != 'N/A':
    if st.button("⚙️ Processar Extratos"):
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
            st.session_state.skipped_indices = []
            undo_manager.clear()

            conn = get_connection()
            regras = pd.read_sql(
                "SELECT * FROM tb_extratos_regras WHERE id_empresa = %s AND banco_nome = %s",
                conn,
                params=(id_empresa, banco_selecionado)
            )
            conn.close()

            prontos, pendentes, linhas_ignoradas_regras = [], [], []

            if not st.session_state.df_bruto.empty:
                for idx, row in st.session_state.df_bruto.iterrows():
                    match = False
                    for _, r in regras.iterrows():
                        if (fuzz.ratio(padronizar_texto(r['termo_chave']), row['Descricao']) >= 85
                                and r['sinal_esperado'] == row['Sinal']):
                            if r['conta_contabil'] == 'IGNORAR':
                                linhas_ignoradas_regras.append(idx)
                            else:
                                debito_conta  = conta_banco_fixa if row['Sinal'] == '+' else r['conta_contabil']
                                credito_conta = r['conta_contabil'] if row['Sinal'] == '+' else conta_banco_fixa
                                prontos.append({
                                    'Debito':    debito_conta,
                                    'Credito':   credito_conta,
                                    'Data':      row['Data'],
                                    'Valor':     f"{row['Valor']:.2f}".replace('.', ','),
                                    'Historico': r['historico_padrao'] if r['historico_padrao'] else row['Descricao']
                                })
                            match = True
                            break
                    if not match:
                        pendentes.append({'idx_original': idx, **row})

            st.session_state.prontos                 = prontos
            st.session_state.pendentes               = pd.DataFrame(pendentes)
            st.session_state.linhas_ignoradas_regras = linhas_ignoradas_regras
            st.session_state.criticas                = criticas
            st.session_state.comuns                  = comuns
            st.success("Processamento concluído!")
            st.rerun()
elif conta_banco_fixa == 'N/A':
    st.error("Configure a conta contábil antes de processar.")

# =============================================================================
# 5º PASSO: RESULTADOS + MESA DE TREINAMENTO
# =============================================================================
if not st.session_state.df_bruto.empty:

    st.divider()
    df_validos = st.session_state.df_bruto[
        ~st.session_state.df_bruto.index.isin(st.session_state.linhas_ignoradas_regras)
    ]
    total_e                = df_validos[df_validos['Sinal'] == '+']['Valor'].sum()
    total_s                = df_validos[df_validos['Sinal'] == '-']['Valor'].sum()
    saldo_final_calculado  = saldo_anterior_informado + total_e - total_s

    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Saldo Anterior",           formatar_moeda(saldo_anterior_informado))
    c2.metric("🟢 Entradas Válidas",      formatar_moeda(total_e))
    c3.metric("🔴 Saídas Válidas",        formatar_moeda(total_s))
    c4.metric("⚖️ Saldo Final Calculado", formatar_moeda(saldo_final_calculado))

    # Alertas de leitura
    if st.session_state.criticas or st.session_state.comuns:
        with st.expander(
            f"⚠️ Alertas de Leitura ({len(st.session_state.criticas)} críticas / {len(st.session_state.comuns)} informativas)"
        ):
            if st.session_state.criticas:
                st.error("Linhas com valores suspeitos (possíveis transações não lidas):")
                for l in list(dict.fromkeys(st.session_state.criticas)):
                    st.code(l)
            if st.session_state.comuns:
                st.info("Ruídos e textos informativos ignorados:")
                for l in list(dict.fromkeys(st.session_state.comuns))[:20]:
                    st.text(l)

    # Mesa de Treinamento
    df_p = st.session_state.pendentes
    if not df_p.empty:
        fila = df_p[~df_p['idx_original'].isin(st.session_state.skipped_indices)]
        if not fila.empty:
            item = fila.iloc[0]
            st.subheader("🎓 Mesa de Treinamento")

            m1, m2, m3, m4, m5 = st.columns([1, 1, 1, 3, 1])
            m1.metric("📅 Data",  item['Data'])
            m2.metric("💰 Valor", formatar_moeda(item['Valor']))
            m3.metric("↕️ Tipo",  "🟢 Entrada" if item['Sinal'] == '+' else "🔴 Saída")
            m4.write(f"**Descrição Extraída:** {item['Descricao']}")

            if not undo_manager.is_empty():
                if m5.button("↩️ Desfazer Ação"):
                    ultima_acao = undo_manager.pop()
                    conn = None
                    try:
                        conn = get_connection()
                        cursor = conn.cursor()
                        t, d  = ultima_acao['type'], ultima_acao['data']
                        if t in ('salvar_regra', 'ignorar_lixo'):
                            cursor.execute("DELETE FROM tb_extratos_regras WHERE id = %s", (d['id_regra'],))
                            conn.commit()
                            st.toast(f"Regra ID {d['id_regra']} desfeita!")
                        elif t == 'pular':
                            if d['idx'] in st.session_state.skipped_indices:
                                st.session_state.skipped_indices.remove(d['idx'])
                            st.toast("Pulo desfeito!")
                        elif t == 'adicionar_conta_banco':
                            cursor.execute("DELETE FROM empresa_banco_contas WHERE id = %s", (d['id_conta'],))
                            conn.commit()
                            st.toast("Conta adicionada desfeita!")
                        elif t == 'editar_conta_banco':
                            cursor.execute(
                                "UPDATE empresa_banco_contas SET nome_banco=%s, conta_contabil=%s WHERE id=%s",
                                (d['old_nome_banco'], d['old_conta_contabil'], d['id_conta'])
                            )
                            conn.commit()
                            st.toast("Edição de conta desfeita!")
                        elif t == 'deletar_conta_banco':
                            cursor.execute(
                                "INSERT INTO empresa_banco_contas (id, id_empresa, nome_banco, conta_contabil) VALUES (%s,%s,%s,%s)",
                                (d['id_conta'], d['id_empresa'], d['nome_banco'], d['conta_contabil'])
                            )
                            conn.commit()
                            st.toast("Remoção de conta desfeita!")
                    except mysql.connector.Error as err:
                        logging.error(f"Erro ao desfazer: {err}")
                        st.error(f"Erro ao desfazer: {err}")
                    finally:
                        if conn:
                            conn.close()
                    st.rerun()

            palavras_desc = item['Descricao'].split()
            selecionadas  = st.pills(
                "Selecione os termos-chave (ou deixe vazio para a frase inteira):",
                palavras_desc,
                selection_mode="multi"
            )
            termo_final = " ".join(selecionadas) if selecionadas else item['Descricao']

            if termo_final:
                df_impactados = df_p[df_p['Descricao'].str.contains(re.escape(termo_final), case=False, na=False)]
                impacto = len(df_impactados)
                if impacto > 0:
                    st.info(f"💡 Esta regra resolverá **{impacto}** lançamento(s) desta fila.")
                    with st.expander(f"📋 Ver lançamentos impactados ({impacto})", expanded=False):
                        st.dataframe(
                            df_impactados[['Data', 'Descricao', 'Valor', 'Sinal']].reset_index(drop=True),
                            use_container_width=True
                        )

            with st.form("form_treino"):
                st.caption(f"A regra atuará sobre: **{termo_final}**")
                f1, f2, f3 = st.columns(3)
                contra = f1.text_input("Contrapartida (Conta Contábil)")
                cod_h  = f2.text_input("Cód. Hist. (Opcional)")
                txt_h  = f3.text_input("Histórico Padrão (Opcional)")
                b1, b2, b3, b4 = st.columns(4)

                if b1.form_submit_button("✅ Salvar Regra"):
                    if contra:
                        conn = None
                        try:
                            conn = get_connection()
                            cursor = conn.cursor()
                            cursor.execute(
                                "INSERT INTO tb_extratos_regras (id_empresa, banco_nome, termo_chave, sinal_esperado, conta_contabil, cod_historico_erp, historico_padrao) VALUES (%s,%s,%s,%s,%s,%s,%s)",
                                (id_empresa, banco_selecionado, termo_final, item['Sinal'], contra, cod_h, txt_h)
                            )
                            id_inserido = cursor.lastrowid
                            conn.commit()
                            undo_manager.push('salvar_regra', {'id_regra': id_inserido})
                            st.success("Regra salva!")
                        except mysql.connector.Error as err:
                            logging.error(f"Erro ao salvar regra: {err}")
                            st.error(f"Erro ao salvar regra: {err}")
                        finally:
                            if conn:
                                conn.close()
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
                        st.success("Regra de ignorar salva!")
                    except mysql.connector.Error as err:
                        logging.error(f"Erro ao ignorar lixo: {err}")
                        st.error(f"Erro ao ignorar: {err}")
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
                    st.info("Fila e histórico resetados.")
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

# =============================================================================
# GERENCIAMENTO DE REGRAS
# =============================================================================
st.divider()
with st.expander("📚 Gerenciar Regras Cadastradas", expanded=False):
    conn = None
    regras_v = pd.DataFrame()
    try:
        conn = get_connection()
        regras_v = pd.read_sql(
            "SELECT * FROM tb_extratos_regras WHERE id_empresa = %s AND banco_nome = %s ORDER BY id DESC",
            conn,
            params=(id_empresa, banco_selecionado)
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
                        conn = get_connection()
                        cursor = conn.cursor()
                        cursor.execute(
                            "UPDATE tb_extratos_regras SET termo_chave=%s, conta_contabil=%s, sinal_esperado=%s, cod_historico_erp=%s, historico_padrao=%s WHERE id=%s",
                            (novo_termo, nova_conta, novo_sinal, novo_cod_hist, novo_hist, regra_para_editar['id'])
                        )
                        conn.commit()
                        st.success("Regra atualizada!")
                        st.session_state.editando_regra_id = None
                    except mysql.connector.Error as err:
                        st.error(f"Erro ao atualizar: {err}")
                    finally:
                        if conn:
                            conn.close()
                    st.rerun()
                if st.form_submit_button("Cancelar"):
                    st.session_state.editando_regra_id = None
                    st.rerun()

# =============================================================================
# GERENCIAMENTO DE CONTAS POR BANCO
# =============================================================================
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
                    undo_manager.push('deletar_conta_banco', {
                        'id_conta': c['id'], 'id_empresa': id_empresa,
                        'nome_banco': c['nome_banco'], 'conta_contabil': c['conta_contabil']
                    })
                    st.toast("Conta deletada!")
                except mysql.connector.Error as err:
                    st.error(f"Erro ao deletar: {err}")
                finally:
                    if conn:
                        conn.close()
                st.rerun()
    else:
        st.info("Nenhuma conta cadastrada para esta empresa.")

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
                        old = df_contas_banco[df_contas_banco['id'] == st.session_state.editando_conta_banco_id].iloc[0]
                        undo_manager.push('editar_conta_banco', {
                            'id_conta': st.session_state.editando_conta_banco_id,
                            'old_nome_banco': old['nome_banco'],
                            'old_conta_contabil': old['conta_contabil']
                        })
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
                        undo_manager.push('adicionar_conta_banco', {'id_conta': cursor.lastrowid})
                        st.success("Conta adicionada!")
                    conn.commit()
                    st.session_state.editando_conta_banco_id = None
                except mysql.connector.Error as err:
                    st.error(f"Erro ao salvar conta: {err}")
                finally:
                    if conn:
                        conn.close()
                st.rerun()
            else:
                st.error("Preencha todos os campos.")

        if col_cb2.form_submit_button("Cancelar"):
            st.session_state.editando_conta_banco_id = None
            st.rerun()
