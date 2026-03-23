import streamlit as st
import mysql.connector
import pandas as pd
import requests
import io
from datetime import date, datetime
from calendar import monthrange
from decimal import Decimal, ROUND_HALF_UP
from fpdf import FPDF

st.set_page_config(
    page_title="Crescere - PIS/COFINS",
    layout="wide",
    page_icon="🛡️"
)

st.markdown("""
<style>
    .main {
        background-color: #f5f7f9;
    }
    .block-container {
        padding-top: 1.5rem;
        padding-bottom: 1rem;
    }
    .stButton > button {
        width: 100%;
        border-radius: 6px;
        height: 2.9em;
        background-color: #004b87;
        color: white;
        border: 0;
        font-weight: 600;
    }
    .stButton > button:hover {
        background-color: #003964;
        color: white;
    }
    .stDownloadButton > button {
        width: 100%;
        border-radius: 6px;
        height: 2.9em;
        font-weight: 600;
    }
    .stTextInput > div > div > input,
    .stTextArea textarea,
    .stSelectbox div[data-baseweb="select"] > div,
    .stNumberInput input {
        border-radius: 6px !important;
    }
    .top-card {
        background: white;
        padding: 14px 18px;
        border-radius: 10px;
        border: 1px solid #e8ecef;
        margin-bottom: 12px;
    }
    .small-muted {
        color: #6c757d;
        font-size: 0.92rem;
    }
    .section-title {
        font-size: 1.15rem;
        font-weight: 700;
        color: #1f2937;
        margin-bottom: 8px;
    }
</style>
""", unsafe_allow_html=True)

MESES = [
    "Janeiro", "Fevereiro", "Março", "Abril", "Maio", "Junho",
    "Julho", "Agosto", "Setembro", "Outubro", "Novembro", "Dezembro"
]

OPERACOES = [
    "Compra Mercador/Insumos",
    "Combustível (Diesel)",
    "Manutenção",
    "Depreciação",
    "Locação Aluguel (PJ)",
    "Energia Elétrica",
    "Fretes",
    "Serviço c/ sessão de mão de obra",
    "Venda de Mercadorias / Produtos",
    "Venda de Serviços",
    "Receita Financeira"
]

MAPEAMENTO_ERP = {
    "Compra Mercador/Insumos": {"debito": "3101", "credito": "2101", "historico": "COMPRA MERCADORIA/INSUMOS"},
    "Combustível (Diesel)": {"debito": "3102", "credito": "2101", "historico": "COMBUSTIVEL DIESEL"},
    "Manutenção": {"debito": "3103", "credito": "2101", "historico": "MANUTENCAO"},
    "Depreciação": {"debito": "3104", "credito": "1601", "historico": "DEPRECIACAO"},
    "Locação Aluguel (PJ)": {"debito": "3105", "credito": "2101", "historico": "LOCACAO ALUGUEL PJ"},
    "Energia Elétrica": {"debito": "3106", "credito": "2101", "historico": "ENERGIA ELETRICA"},
    "Fretes": {"debito": "3107", "credito": "2101", "historico": "FRETES"},
    "Serviço c/ sessão de mão de obra": {"debito": "3108", "credito": "2101", "historico": "SERVICO CESSAO DE MAO DE OBRA"},
    "Venda de Mercadorias / Produtos": {"debito": "1101", "credito": "4101", "historico": "VENDA DE MERCADORIAS / PRODUTOS"},
    "Venda de Serviços": {"debito": "1101", "credito": "4102", "historico": "VENDA DE SERVICOS"},
    "Receita Financeira": {"debito": "1101", "credito": "4201", "historico": "RECEITA FINANCEIRA"}
}

def estado_form_empresa():
    return {
        "id": None,
        "nome": "",
        "fantasia": "",
        "cnpj": "",
        "regime": "Lucro Real",
        "tipo": "Matriz",
        "cnae": "",
        "endereco": ""
    }

if "dados_form" not in st.session_state:
    st.session_state.dados_form = estado_form_empresa()

if "itens_apuracao" not in st.session_state:
    st.session_state.itens_apuracao = []

if "filtro_empresa" not in st.session_state:
    st.session_state.filtro_empresa = ""

def get_db_connection():
    return mysql.connector.connect(**st.secrets["mysql"])

def limpar_cnpj(cnpj):
    return "".join(filter(str.isdigit, str(cnpj)))

def formatar_cnpj(cnpj):
    cnpj = limpar_cnpj(cnpj)
    if len(cnpj) != 14:
        return cnpj
    return f"{cnpj[:2]}.{cnpj[2:5]}.{cnpj[5:8]}/{cnpj[8:12]}-{cnpj[12:]}"

def decimal_2(valor):
    return Decimal(str(valor)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)

def formata_real(valor):
    valor = float(valor or 0)
    return f"R$ {valor:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")

def ultimo_dia_mes(ano, mes):
    ultimo = monthrange(ano, mes)[1]
    return date(ano, mes, ultimo)

def reset_form_empresa():
    st.session_state.dados_form = estado_form_empresa()

def consultar_cnpj(cnpj_limpo):
    url = f"https://receitaws.com.br/v1/cnpj/{cnpj_limpo}"
    try:
        response = requests.get(url, timeout=15)
        if response.status_code == 200:
            return response.json()
    except requests.RequestException:
        return None
    return None

def montar_endereco_receita(dados):
    partes = [
        dados.get("logradouro", ""),
        dados.get("numero", ""),
        dados.get("complemento", ""),
        dados.get("bairro", ""),
        dados.get("municipio", ""),
        dados.get("uf", ""),
        dados.get("cep", "")
    ]
    partes = [p for p in partes if str(p).strip()]
    return ", ".join(partes)

def calcular_aliquotas(operacao):
    if operacao == "Receita Financeira":
        return Decimal("0.0065"), Decimal("0.04")
    return Decimal("0.0165"), Decimal("0.076")

def calcular_impostos(operacao, valor):
    valor_dec = decimal_2(valor)
    aliq_pis, aliq_cofins = calcular_aliquotas(operacao)
    pis = decimal_2(valor_dec * aliq_pis)
    cofins = decimal_2(valor_dec * aliq_cofins)
    return valor_dec, pis, cofins

def listar_empresas(filtro=""):
    conn = get_db_connection()
    cursor = conn.cursor(dictionary=True)

    sql = """
        SELECT id, nome, fantasia, cnpj, regime, tipo, cnae, endereco
        FROM empresas
        WHERE 1=1
    """
    params = []

    if filtro:
        sql += " AND (nome LIKE %s OR cnpj LIKE %s)"
        termo = f"%{filtro}%"
        params.extend([termo, termo])

    sql += " ORDER BY nome"
    cursor.execute(sql, params)
    rows = cursor.fetchall()
    conn.close()
    return pd.DataFrame(rows)

def buscar_empresa_por_id(empresa_id):
    conn = get_db_connection()
    cursor = conn.cursor(dictionary=True)
    cursor.execute("""
        SELECT id, nome, fantasia, cnpj, regime, tipo, cnae, endereco
        FROM empresas
        WHERE id = %s
    """, (empresa_id,))
    row = cursor.fetchone()
    conn.close()
    return row

def cnpj_ja_existe(cnpj_limpo, id_atual=None):
    conn = get_db_connection()
    cursor = conn.cursor()
    if id_atual:
        cursor.execute("SELECT COUNT(*) FROM empresas WHERE cnpj = %s AND id <> %s", (cnpj_limpo, id_atual))
    else:
        cursor.execute("SELECT COUNT(*) FROM empresas WHERE cnpj = %s", (cnpj_limpo,))
    total = cursor.fetchone()[0]
    conn.close()
    return total > 0

def salvar_empresa(dados):
    conn = get_db_connection()
    cursor = conn.cursor()

    if dados["id"]:
        sql = """
            UPDATE empresas
               SET nome=%s,
                   fantasia=%s,
                   cnpj=%s,
                   regime=%s,
                   tipo=%s,
                   cnae=%s,
                   endereco=%s
             WHERE id=%s
        """
        params = (
            dados["nome"],
            dados["fantasia"],
            dados["cnpj"],
            dados["regime"],
            dados["tipo"],
            dados["cnae"],
            dados["endereco"],
            dados["id"]
        )
    else:
        sql = """
            INSERT INTO empresas (nome, fantasia, cnpj, regime, tipo, cnae, endereco)
            VALUES (%s, %s, %s, %s, %s, %s, %s)
        """
        params = (
            dados["nome"],
            dados["fantasia"],
            dados["cnpj"],
            dados["regime"],
            dados["tipo"],
            dados["cnae"],
            dados["endereco"]
        )

    cursor.execute(sql, params)
    conn.commit()
    conn.close()

def salvar_apuracao_no_banco(empresa_id, competencia, itens):
    conn = get_db_connection()
    cursor = conn.cursor()

    for item in itens:
        sql = """
            INSERT INTO historico_apuracoes
            (empresa_id, competencia, operacao, valor, pis, cofins, debito, credito, historico)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
        """
        params = (
            empresa_id,
            competencia,
            item["operacao"],
            float(item["valor"]),
            float(item["pis"]),
            float(item["cofins"]),
            item["debito"],
            item["credito"],
            item["historico"]
        )
        cursor.execute(sql, params)

    conn.commit()
    conn.close()

class PDFRelatorio(FPDF):
    def header(self):
        self.set_fill_color(0, 75, 135)
        self.rect(0, 0, 210, 22, "F")
        self.set_text_color(255, 255, 255)
        self.set_font("Arial", "B", 14)
        self.cell(0, 12, "CRESCERE - RELATÓRIO DE APURAÇÃO PIS/COFINS", 0, 1, "C")
        self.ln(2)
        self.set_text_color(0, 0, 0)

    def footer(self):
        self.set_y(-12)
        self.set_font("Arial", "I", 8)
        self.set_text_color(120, 120, 120)
        self.cell(0, 8, f"Página {self.page_no()}", 0, 0, "C")

def gerar_pdf_apuracao(empresa, competencia, itens_df):
    pdf = PDFRelatorio()
    pdf.set_auto_page_break(auto=True, margin=15)
    pdf.add_page()

    pdf.set_font("Arial", "", 10)
    pdf.cell(0, 7, f"Empresa: {empresa['nome']}", 0, 1)
    pdf.cell(0, 7, f"CNPJ: {formatar_cnpj(empresa['cnpj'])}", 0, 1)
    pdf.cell(0, 7, f"Regime: {empresa['regime']}", 0, 1)
    pdf.cell(0, 7, f"Competência: {competencia.strftime('%d/%m/%Y')}", 0, 1)
    pdf.cell(0, 7, f"Emitido em: {datetime.now().strftime('%d/%m/%Y %H:%M')}", 0, 1)
    pdf.ln(4)

    pdf.set_font("Arial", "B", 9)
    pdf.set_fill_color(220, 230, 241)
    pdf.cell(70, 8, "Operação", 1, 0, "L", True)
    pdf.cell(30, 8, "Base", 1, 0, "R", True)
    pdf.cell(30, 8, "PIS", 1, 0, "R", True)
    pdf.cell(30, 8, "COFINS", 1, 0, "R", True)
    pdf.cell(30, 8, "Total", 1, 1, "R", True)

    pdf.set_font("Arial", "", 8)
    total_base = Decimal("0.00")
    total_pis = Decimal("0.00")
    total_cofins = Decimal("0.00")

    for _, row in itens_df.iterrows():
        total_linha = decimal_2(Decimal(str(row["pis"])) + Decimal(str(row["cofins"])))
        total_base += Decimal(str(row["valor"]))
        total_pis += Decimal(str(row["pis"]))
        total_cofins += Decimal(str(row["cofins"]))

        pdf.cell(70, 8, str(row["operacao"])[:38], 1, 0, "L")
        pdf.cell(30, 8, formata_real(row["valor"]), 1, 0, "R")
        pdf.cell(30, 8, formata_real(row["pis"]), 1, 0, "R")
        pdf.cell(30, 8, formata_real(row["cofins"]), 1, 0, "R")
        pdf.cell(30, 8, formata_real(total_linha), 1, 1, "R")

    pdf.set_font("Arial", "B", 9)
    pdf.cell(70, 8, "Totais", 1, 0, "L", True)
    pdf.cell(30, 8, formata_real(total_base), 1, 0, "R", True)
    pdf.cell(30, 8, formata_real(total_pis), 1, 0, "R", True)
    pdf.cell(30, 8, formata_real(total_cofins), 1, 0, "R", True)
    pdf.cell(30, 8, formata_real(total_pis + total_cofins), 1, 1, "R", True)

    return pdf.output(dest="S").encode("latin-1")

def montar_df_erp(itens, competencia):
    linhas = []
    data_str = competencia.strftime("%d/%m/%Y")

    for item in itens:
        linhas.append({
            "Debito": item["debito"],
            "Credito": item["credito"],
            "Data": data_str,
            "Valor": float(item["valor"]),
            "Historico": item["historico"]
        })

    return pd.DataFrame(linhas, columns=["Debito", "Credito", "Data", "Valor", "Historico"])

def gerar_excel_em_memoria(df):
    output = io.BytesIO()
    with pd.ExcelWriter(output, engine="openpyxl") as writer:
        df.to_excel(writer, index=False, sheet_name="ERP")
    output.seek(0)
    return output

with st.sidebar:
    st.title("🛡️ Crescere")
    st.caption("Apuração PIS/COFINS")
    menu = st.radio("Módulos", ["Início", "Gestão de Empresas", "Apuração Mensal", "Relatórios & Exportação"])

if menu == "Início":
    st.markdown('<div class="section-title">Visão Geral</div>', unsafe_allow_html=True)

    c1, c2, c3 = st.columns(3)
    with c1:
        st.markdown('<div class="top-card"><b>Empresas</b><br><span class="small-muted">Cadastro e edição de unidades</span></div>', unsafe_allow_html=True)
    with c2:
        st.markdown('<div class="top-card"><b>Apuração Mensal</b><br><span class="small-muted">Lançamento e cálculo de PIS/COFINS</span></div>', unsafe_allow_html=True)
    with c3:
        st.markdown('<div class="top-card"><b>Relatórios & ERP</b><br><span class="small-muted">PDF, CSV e Excel para integração</span></div>', unsafe_allow_html=True)

elif menu == "Gestão de Empresas":
    st.markdown('<div class="section-title">Gestão de Empresas</div>', unsafe_allow_html=True)

    tab_form, tab_lista = st.tabs(["Cadastro / Edição", "Empresas Cadastradas"])

    with tab_form:
        st.markdown("### Consulta de CNPJ")

        c1, c2 = st.columns([4, 1])
        cnpj_consulta = c1.text_input("Consultar CNPJ para preenchimento automático", placeholder="00.000.000/0000-00")
        if c2.button("Consultar"):
            cnpj_limpo = limpar_cnpj(cnpj_consulta)
            if len(cnpj_limpo) != 14:
                st.warning("Informe um CNPJ válido com 14 dígitos.")
            else:
                dados = consultar_cnpj(cnpj_limpo)
                if dados and dados.get("status") != "ERROR":
                    st.session_state.dados_form.update({
                        "nome": dados.get("nome", ""),
                        "fantasia": dados.get("fantasia", ""),
                        "cnpj": limpar_cnpj(dados.get("cnpj", "")),
                        "cnae": dados["atividade_principal"][0].get("code", "") if dados.get("atividade_principal") else "",
                        "endereco": montar_endereco_receita(dados)
                    })
                    st.rerun()
                else:
                    st.warning("Não foi possível consultar esse CNPJ no momento.")

        st.markdown("### Dados da Empresa")
        f = st.session_state.dados_form

        with st.form("form_empresa", clear_on_submit=False):
            col1, col2 = st.columns(2)
            nome = col1.text_input("Razão Social", value=f["nome"])
            fantasia = col2.text_input("Nome Fantasia", value=f["fantasia"])

            col3, col4, col5 = st.columns([2, 2, 1])
            cnpj = col3.text_input("CNPJ", value=formatar_cnpj(f["cnpj"]) if f["cnpj"] else "")
            regime = col4.selectbox("Regime Tributário", ["Lucro Real", "Lucro Presumido"], index=0 if f["regime"] == "Lucro Real" else 1)
            tipo = col5.selectbox("Tipo", ["Matriz", "Filial"], index=0 if f["tipo"] == "Matriz" else 1)

            cnae = st.text_input("CNAE", value=f["cnae"])
            endereco = st.text_area("Endereço", value=f["endereco"], height=90)

            b1, b2 = st.columns([2, 1])
            salvar = b1.form_submit_button("Salvar")
            cancelar = b2.form_submit_button("Cancelar edição")

            if cancelar:
                reset_form_empresa()
                st.rerun()

            if salvar:
                cnpj_limpo = limpar_cnpj(cnpj)

                if not nome.strip():
                    st.warning("Informe a razão social.")
                elif len(cnpj_limpo) != 14:
                    st.warning("Informe um CNPJ válido.")
                elif cnpj_ja_existe(cnpj_limpo, f["id"]):
                    st.warning("Já existe uma empresa cadastrada com esse CNPJ.")
                else:
                    dados_salvar = {
                        "id": f["id"],
                        "nome": nome.strip(),
                        "fantasia": fantasia.strip(),
                        "cnpj": cnpj_limpo,
                        "regime": regime,
                        "tipo": tipo,
                        "cnae": cnae.strip(),
                        "endereco": endereco.strip()
                    }
                    salvar_empresa(dados_salvar)
                    reset_form_empresa()
                    st.success("Cadastro atualizado com sucesso.")
                    st.rerun()

    with tab_lista:
        filtro = st.text_input("Buscar por nome ou CNPJ", value=st.session_state.filtro_empresa)
        st.session_state.filtro_empresa = filtro

        df_empresas = listar_empresas(filtro=filtro)

        if df_empresas.empty:
            st.info("Nenhuma empresa encontrada.")
        else:
            for _, row in df_empresas.iterrows():
                with st.container():
                    c1, c2 = st.columns([6, 1])
                    c1.markdown(
                        f"**{row['nome']}**  \n"
                        f"CNPJ: {formatar_cnpj(row['cnpj'])} | Tipo: {row['tipo']} | Regime: {row['regime']}"
                    )
                    if c2.button("Editar", key=f"editar_empresa_{row['id']}"):
                        st.session_state.dados_form = {
                            "id": row["id"],
                            "nome": row["nome"] or "",
                            "fantasia": row["fantasia"] or "",
                            "cnpj": row["cnpj"] or "",
                            "regime": row["regime"] or "Lucro Real",
                            "tipo": row["tipo"] or "Matriz",
                            "cnae": row["cnae"] or "",
                            "endereco": row["endereco"] or ""
                        }
                        st.rerun()
                    st.divider()

elif menu == "Apuração Mensal":
    st.markdown('<div class="section-title">Apuração Mensal</div>', unsafe_allow_html=True)

    df_empresas = listar_empresas()

    if df_empresas.empty:
        st.warning("Cadastre ao menos uma empresa antes de iniciar a apuração.")
    else:
        opcoes_empresas = {
            f"{row['nome']} - {formatar_cnpj(row['cnpj'])}": row["id"]
            for _, row in df_empresas.iterrows()
        }

        col_a, col_b, col_c = st.columns([3, 1, 1])

        empresa_label = col_a.selectbox("Empresa", list(opcoes_empresas.keys()))
        empresa_id = opcoes_empresas[empresa_label]
        empresa = buscar_empresa_por_id(empresa_id)

        hoje = date.today()
        mes_padrao = hoje.month - 1 if hoje.month > 1 else 12
        ano_padrao = hoje.year if hoje.month > 1 else hoje.year - 1

        mes_nome = col_b.selectbox("Mês", MESES, index=mes_padrao - 1)
        ano = col_c.number_input("Ano", min_value=2020, max_value=2100, value=ano_padrao, step=1)

        mes_num = MESES.index(mes_nome) + 1
        competencia = ultimo_dia_mes(int(ano), mes_num)

        st.markdown("### Incluir operação")

        with st.form("form_incluir_operacao", clear_on_submit=True):
            c1, c2 = st.columns([3, 2])
            operacao = c1.selectbox("Operação", OPERACOES)
            valor = c2.number_input("Valor", min_value=0.0, format="%.2f", step=100.00)

            add = st.form_submit_button("Incluir item")

            if add:
                if valor <= 0:
                    st.warning("Informe um valor maior que zero.")
                else:
                    valor_calc, pis, cofins = calcular_impostos(operacao, valor)
                    mapa = MAPEAMENTO_ERP.get(operacao, {"debito": "", "credito": "", "historico": operacao.upper()})

                    st.session_state.itens_apuracao.append({
                        "operacao": operacao,
                        "valor": float(valor_calc),
                        "pis": float(pis),
                        "cofins": float(cofins),
                        "debito": mapa["debito"],
                        "credito": mapa["credito"],
                        "historico": mapa["historico"],
                        "data": competencia.strftime("%d/%m/%Y")
                    })
                    st.rerun()

        st.markdown("### Itens lançados")

        if not st.session_state.itens_apuracao:
            st.info("Nenhum item incluído nesta apuração.")
        else:
            df_itens = pd.DataFrame(st.session_state.itens_apuracao)
            df_visual = df_itens[["operacao", "valor", "pis", "cofins", "debito", "credito", "historico", "data"]].copy()

            df_visual["valor"] = df_visual["valor"].apply(formata_real)
            df_visual["pis"] = df_visual["pis"].apply(formata_real)
            df_visual["cofins"] = df_visual["cofins"].apply(formata_real)

            st.dataframe(df_visual, use_container_width=True, hide_index=True)

            total_base = sum(Decimal(str(x)) for x in df_itens["valor"])
            total_pis = sum(Decimal(str(x)) for x in df_itens["pis"])
            total_cofins = sum(Decimal(str(x)) for x in df_itens["cofins"])

            r1, r2, r3 = st.columns(3)
            r1.metric("Base Total", formata_real(total_base))
            r2.metric("PIS Total", formata_real(total_pis))
            r3.metric("COFINS Total", formata_real(total_cofins))

            c1, c2, c3 = st.columns(3)

            if c1.button("Limpar itens"):
                st.session_state.itens_apuracao = []
                st.rerun()

            if c2.button("Salvar apuração no banco"):
                try:
                    salvar_apuracao_no_banco(empresa_id, competencia, st.session_state.itens_apuracao)
                    st.success("Apuração gravada com sucesso.")
                except Exception:
                    st.warning("A gravação da apuração precisa ser ajustada aos nomes reais das colunas da tabela historico_apuracoes.")

            if c3.button("Preparar relatório e exportação"):
                st.success("Itens prontos para relatório e exportação.")

elif menu == "Relatórios & Exportação":
    st.markdown('<div class="section-title">Relatórios & Exportação</div>', unsafe_allow_html=True)

    df_empresas = listar_empresas()

    if df_empresas.empty:
        st.warning("Cadastre uma empresa para gerar relatórios.")
    elif not st.session_state.itens_apuracao:
        st.warning("Inclua itens na apuração mensal antes de exportar.")
    else:
        opcoes_empresas = {
            f"{row['nome']} - {formatar_cnpj(row['cnpj'])}": row["id"]
            for _, row in df_empresas.iterrows()
        }

        c1, c2, c3 = st.columns([3, 1, 1])

        empresa_label = c1.selectbox("Empresa para emissão", list(opcoes_empresas.keys()))
        empresa_id = opcoes_empresas[empresa_label]
        empresa = buscar_empresa_por_id(empresa_id)

        hoje = date.today()
        mes_padrao = hoje.month - 1 if hoje.month > 1 else 12
        ano_padrao = hoje.year if hoje.month > 1 else hoje.year - 1

        mes_nome = c2.selectbox("Mês de referência", MESES, index=mes_padrao - 1)
        ano = c3.number_input("Ano de referência", min_value=2020, max_value=2100, value=ano_padrao, step=1)

        competencia = ultimo_dia_mes(int(ano), MESES.index(mes_nome) + 1)
        df_itens = pd.DataFrame(st.session_state.itens_apuracao)

        st.markdown("### Resumo pronto para emissão")
        st.dataframe(df_itens, use_container_width=True, hide_index=True)

        col_pdf, col_csv, col_xlsx = st.columns(3)

        pdf_bytes = gerar_pdf_apuracao(empresa, competencia, df_itens)

        with col_pdf:
            st.download_button(
                "Baixar PDF",
                data=pdf_bytes,
                file_name=f"apuracao_{limpar_cnpj(empresa['cnpj'])}_{competencia.strftime('%m_%Y')}.pdf",
                mime="application/pdf"
            )

        df_erp = montar_df_erp(st.session_state.itens_apuracao, competencia)

        with col_csv:
            csv_data = df_erp.to_csv(index=False, encoding="utf-8-sig").encode("utf-8-sig")
            st.download_button(
                "Baixar CSV ERP",
                data=csv_data,
                file_name=f"erp_{limpar_cnpj(empresa['cnpj'])}_{competencia.strftime('%m_%Y')}.csv",
                mime="text/csv"
            )

        with col_xlsx:
            excel_buffer = gerar_excel_em_memoria(df_erp)
            st.download_button(
                "Baixar Excel ERP",
                data=excel_buffer.getvalue(),
                file_name=f"erp_{limpar_cnpj(empresa['cnpj'])}_{competencia.strftime('%m_%Y')}.xlsx",
                mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
            )
