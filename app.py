import streamlit as st
import mysql.connector
import pandas as pd
import requests
from datetime import date, timedelta
import calendar
from fpdf import FPDF
import io
import os

# --- 1. CONFIGURAÇÕES VISUAIS E ESTADOS ---
st.set_page_config(page_title="Crescere - Apuração Fiscal", layout="wide")

st.markdown("""
<style>
    .stApp { background-color: #f4f6f9; }
    .stButton>button { background-color: #004b87; color: white; border-radius: 4px; border: none; font-weight: 500;}
    .stButton>button:hover { background-color: #003366; color: white; }
    div[data-testid="stForm"], .css-1d391kg { background-color: #ffffff; padding: 20px; border-radius: 8px; box-shadow: 0 1px 3px rgba(0,0,0,0.1); border: 1px solid #e2e8f0;}
    h1, h2, h3, h4 { color: #0f172a; font-weight: 600; font-family: 'Segoe UI', Tahoma, Geneva, Verdana, sans-serif; }
    
    /* Acessibilidade: Destaque visual nos campos */
    .stTextInput input { background-color: #f8fafc; border: 1px solid #cbd5e1; }
    .stTextInput input:focus { border: 2px solid #004b87 !important; background-color: #e6f0fa !important; }
</style>
""", unsafe_allow_html=True)

if 'usuario_logado' not in st.session_state:
    st.session_state.usuario_logado = "Rodrigo"

hoje = date.today()
primeiro_dia_mes_atual = hoje.replace(day=1)
ultimo_dia_mes_anterior = primeiro_dia_mes_atual - timedelta(days=1)
competencia_padrao = ultimo_dia_mes_anterior.strftime("%m/%Y")

if 'dados_form' not in st.session_state:
    st.session_state.dados_form = {"id": None, "nome": "", "fantasia": "", "cnpj": "", "regime": "Lucro Real", "tipo": "Matriz", "cnae": "", "endereco": ""}

if 'rascunho_lancamentos' not in st.session_state:
    st.session_state.rascunho_lancamentos = []

# --- 2. FUNÇÕES BASE E FORMATAÇÃO ---
def get_db_connection():
    return mysql.connector.connect(**st.secrets["mysql"])

def formatar_moeda(valor):
    return f"R$ {valor:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")

def consultar_cnpj(cnpj_limpo):
    url = f"https://receitaws.com.br/v1/cnpj/{cnpj_limpo}"
    try:
        response = requests.get(url, timeout=10)
        return response.json() if response.status_code == 200 else None
    except requests.RequestException:
        return None

# --- 3. GESTÃO DE EMPRESAS ---
def modulo_empresas():
    st.markdown("## Gestão de Empresas")
    tab_cad, tab_lista = st.tabs(["Novo Cadastro", "Unidades Cadastradas"])
    
    with tab_cad:
        c_busca, c_btn = st.columns([3,1])
        with c_busca:
            cnpj_input = st.text_input("🔍 Digite o CNPJ para busca automática na Receita Federal:", placeholder="Apenas números")
        
        with c_btn:
            st.markdown("<div style='margin-top: 28px;'></div>", unsafe_allow_html=True) 
            if st.button("Consultar CNPJ", use_container_width=True):
                res = consultar_cnpj(cnpj_input.replace(".","").replace("/","").replace("-",""))
                if res and res.get('status') != 'ERROR':
                    st.session_state.dados_form.update({
                        "nome": res.get('nome', ''),
                        "fantasia": res.get('fantasia', ''),
                        "cnpj": res.get('cnpj', ''),
                        "cnae": res.get('atividade_principal', [{}])[0].get('code', ''),
                        "endereco": f"{res.get('logradouro', '')}, {res.get('numero', '')} - {res.get('bairro', '')}, {res.get('municipio', '')}/{res.get('uf', '')}"
                    })
                    st.rerun()

        st.divider()
        f = st.session_state.dados_form
        c1, c2 = st.columns(2)
        nome = c1.text_input("Razão Social", value=f['nome'])
        fanta = c2.text_input("Nome Fantasia", value=f['fantasia'])
        
        c3, c4, c5 = st.columns([2, 1.5, 1.5])
        cnpj = c3.text_input("CNPJ do Cadastro", value=f['cnpj'])
        regime = c4.selectbox("Regime Tributário", ["Lucro Real", "Lucro Presumido"], index=0 if f['regime'] == "Lucro Real" else 1)
        tipo = c5.selectbox("Tipo de Unidade", ["Matriz", "Filial"], index=0 if f['tipo'] == "Matriz" else 1)
        
        cnae = st.text_input("CNAE Principal", value=f['cnae'])
        endereco = st.text_area("Endereço Completo", value=f['endereco'])
        
        if st.button("Salvar Empresa", use_container_width=True):
            conn = get_db_connection()
            cursor = conn.cursor()
            cursor.execute("""
            CREATE TABLE IF NOT EXISTS empresas (
                id INT AUTO_INCREMENT PRIMARY KEY,
                nome VARCHAR(255), fantasia VARCHAR(255), cnpj VARCHAR(20),
                regime VARCHAR(50), tipo VARCHAR(50), cnae VARCHAR(20), endereco TEXT
            )""")
            if f['id']: 
                sql = "UPDATE empresas SET nome=%s, fantasia=%s, cnpj=%s, regime=%s, tipo=%s, cnae=%s, endereco=%s WHERE id=%s"
                cursor.execute(sql, (nome, fanta, cnpj, regime, tipo, cnae, endereco, f['id']))
            else: 
                sql = "INSERT INTO empresas (nome, fantasia, cnpj, regime, tipo, cnae, endereco) VALUES (%s,%s,%s,%s,%s,%s,%s)"
                cursor.execute(sql, (nome, fanta, cnpj, regime, tipo, cnae, endereco))
            conn.commit()
            conn.close()
            st.session_state.dados_form = {"id": None, "nome": "", "fantasia": "", "cnpj": "", "regime": "Lucro Real", "tipo": "Matriz", "cnae": "", "endereco": ""}
            st.success("Empresa salva com sucesso.")
            st.rerun()

    with tab_lista:
        conn = get_db_connection()
        try:
            df = pd.read_sql("SELECT id, nome, cnpj, regime, tipo FROM empresas", conn)
            for _, row in df.iterrows():
                col_info, col_btn = st.columns([5, 1])
                col_info.markdown(f"**{row['nome']}** | {row['tipo']}<br>CNPJ: {row['cnpj']} | Regime: {row['regime']}", unsafe_allow_html=True)
                if col_btn.button("Editar", key=f"btn_{row['id']}"):
                    df_edit = pd.read_sql(f"SELECT * FROM empresas WHERE id={row['id']}", conn)
                    st.session_state.dados_form = df_edit.iloc[0].to_dict()
                    st.rerun()
                st.divider()
        except:
            pass
        conn.close()

# --- 4. MÓDULO DE APURAÇÃO ---
def calcular_impostos(valor_base, operacao_nome, regime_empresa):
    if regime_empresa == "Lucro Real":
        if operacao_nome == "Receita Financeira":
            return valor_base * 0.0065, valor_base * 0.0400
        else:
            return valor_base * 0.0165, valor_base * 0.0760
    else:
        return valor_base * 0.0065, valor_base * 0.0300

def modulo_apuracao():
    st.markdown("## Apuração Mensal")
    conn = get_db_connection()
    try:
        df_empresas = pd.read_sql("SELECT id, nome, cnpj, regime FROM empresas", conn)
        df_operacoes = pd.read_sql("SELECT * FROM operacoes ORDER BY tipo DESC, nome ASC", conn)
        
        df_operacoes['nome_exibicao'] = df_operacoes.apply(
            lambda x: f"[DÉBITO] {x['nome']}" if x['tipo'] == 'RECEITA' else f"[CRÉDITO] {x['nome']}", 
            axis=1
        )
    except:
        st.warning("Banco de dados não encontrado. Acesse '⚙️ Parâmetros Contábeis' e faça o Reset do Sistema.")
        conn.close(); return

    if df_empresas.empty:
        st.info("Cadastre uma empresa primeiro."); conn.close(); return

    c_empresa, c_comp, c_user = st.columns([2, 1, 1])
    opcoes_empresas = df_empresas.apply(lambda row: f"{row['nome']} - {row['cnpj']}", axis=1)
    empresa_selecionada = c_empresa.selectbox("Empresa Ativa", opcoes_empresas)
    empresa_id = int(df_empresas.loc[opcoes_empresas == empresa_selecionada].iloc[0]['id'])
    regime_empresa = df_empresas.loc[opcoes_empresas == empresa_selecionada].iloc[0]['regime']
    
    competencia = c_comp.text_input("Competência (MM/AAAA)", value=competencia_padrao)
    c_user.text_input("Usuário Logado", value=st.session_state.usuario_logado, disabled=True)

    st.write("---")
    
    col_entrada, col_rascunho = st.columns([1, 1.2], gap="large")

    with col_entrada:
        st.markdown("#### Inserção de Dados")
        operacao_selecionada = st.selectbox("Operação", df_operacoes['nome_exibicao'].tolist())
        operacao_nome = df_operacoes.loc[df_operacoes['nome_exibicao'] == operacao_selecionada, 'nome'].values[0]
        
        valor_base = st.number_input("Valor da Base (R$)", min_value=0.00, step=100.00, format="%.2f")
        historico = st.text_input("Observação Livre", placeholder="Opcional...")
        
        is_retroativo = st.checkbox("Lançamento Retroativo (Mês Anterior)")
        comp_origem = st.text_input("Mês de Origem", placeholder="MM/AAAA", disabled=not is_retroativo)
        
        if st.button("Adicionar à Lista de Rascunho", use_container_width=True):
            if valor_base > 0:
                vp, vc = calcular_impostos(valor_base, operacao_nome, regime_empresa)
                op_id = int(df_operacoes[df_operacoes['nome'] == operacao_nome].iloc[0]['id'])
                
                st.session_state.rascunho_lancamentos.append({
                    "empresa_id": empresa_id,
                    "operacao_nome": operacao_nome,
                    "operacao_exibicao": operacao_selecionada,
                    "operacao_id": op_id,
                    "valor_base": valor_base,
                    "valor_pis": vp,
                    "valor_cofins": vc,
                    "historico": historico,
                    "is_retroativo": is_retroativo,
                    "comp_origem": comp_origem if is_retroativo else None
                })
                st.rerun()
            else:
                st.error("O valor base deve ser maior que zero.")

    with col_rascunho:
        st.markdown("#### Lista de Rascunho (Pré-Banco)")
        if len(st.session_state.rascunho_lancamentos) > 0:
            
            with st.container(height=380, border=True):
                for i, item in enumerate(st.session_state.rascunho_lancamentos):
                    c_desc, c_val, c_del = st.columns([5, 3, 1])
                    c_desc.markdown(f"**{item['operacao_exibicao']}**")
                    c_val.markdown(f"Base: {formatar_moeda(item['valor_base'])}")
                    
                    if c_del.button("✖", key=f"del_{i}", help="Remover item"):
                        st.session_state.rascunho_lancamentos.pop(i)
                        st.rerun()
                    st.divider()
            
            st.write("") 
            if st.button("💾 Gravar Todos no Banco de Dados", type="primary", use_container_width=True):
                mes_str, ano_str = competencia.split('/')
                competencia_db = f"{ano_str}-{mes_str.zfill(2)}"
                ultimo_dia = calendar.monthrange(int(ano_str), int(mes_str))[1]
                data_lancamento = f"{ano_str}-{mes_str.zfill(2)}-{ultimo_dia:02d}"
                
                cursor = conn.cursor()
                for item in st.session_state.rascunho_lancamentos:
                    cursor.execute("""
                        INSERT INTO lancamentos 
                        (empresa_id, operacao_id, competencia, data_lancamento, valor_base, valor_pis, valor_cofins, historico, origem_retroativa, competencia_origem, usuario_registro, status_auditoria) 
                        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, 'ATIVO')""", 
                        (item['empresa_id'], item['operacao_id'], competencia_db, data_lancamento, item['valor_base'], item['valor_pis'], item['valor_cofins'], item['historico'], item['is_retroativo'], item['comp_origem'], st.session_state.usuario_logado))
                
                conn.commit()
                st.session_state.rascunho_lancamentos = []
                st.success("Lançamentos gravados e auditados com sucesso!")
                st.rerun()
        else:
            st.info("Sua lista de rascunho está vazia.")

    st.write("---")
    st.markdown("#### Extrato Consolidado e Retificação (Padrão SAP)")
    try:
        m, a = competencia.split('/'); comp_db = f"{a}-{m.zfill(2)}"
        df_lanc = pd.read_sql(f"""SELECT l.id, o.nome as operacao, l.valor_base, l.valor_pis, l.valor_cofins, l.historico FROM lancamentos l JOIN operacoes o ON l.operacao_id = o.id WHERE l.empresa_id = {empresa_id} AND l.competencia = '{comp_db}' AND l.status_auditoria = 'ATIVO' ORDER BY l.id DESC""", conn)
        
        if not df_lanc.empty:
            df_view = df_lanc.copy()
            df_view['valor_base'] = df_view['valor_base'].apply(formatar_moeda)
            df_view['valor_pis'] = df_view['valor_pis'].apply(formatar_moeda)
            df_view['valor_cofins'] = df_view['valor_cofins'].apply(formatar_moeda)
            df_view.rename(columns={'operacao': 'Operação', 'valor_base': 'Base de Cálculo', 'valor_pis': 'PIS', 'valor_cofins': 'COFINS', 'historico': 'Observação'}, inplace=True)
            st.dataframe(df_view[['id', 'Operação', 'Base de Cálculo', 'PIS', 'COFINS', 'Observação']], use_container_width=True, hide_index=True)
            
            with st.expander("✏️ Retificar um Lançamento Gravado"):
                c_id, c_novo_val, c_motivo = st.columns([1, 2, 3])
                id_retificar = c_id.number_input("ID do Lançamento", min_value=0, step=1)
                novo_valor_base = c_novo_val.number_input("Novo Valor Base (R$)", min_value=0.01, step=100.00)
                motivo = c_motivo.text_input("Justificativa (Obrigatório)", placeholder="Motivo da alteração...")
                
                if st.button("Processar Retificação Segura"):
                    if not motivo:
                        st.error("A justificativa é obrigatória para retificar.")
                    elif id_retificar not in df_lanc['id'].values:
                        st.error("ID inválido ou lançamento não pertence a esta competência.")
                    else:
                        cursor = conn.cursor(dictionary=True)
                        cursor.execute(f"SELECT * FROM lancamentos WHERE id = {id_retificar}")
                        reg_antigo = cursor.fetchone()
                        
                        cursor.execute(f"UPDATE lancamentos SET status_auditoria = 'INATIVO', motivo_alteracao = %s, data_alteracao = CURRENT_TIMESTAMP WHERE id = %s", (f"Retificado. Motivo: {motivo}", id_retificar))
                        
                        cursor.execute(f"SELECT nome FROM operacoes WHERE id = {reg_antigo['operacao_id']}")
                        nome_op = cursor.fetchone()['nome']
                        novo_vp, novo_vc = calcular_impostos(novo_valor_base, nome_op, regime_empresa)
                        
                        cursor.execute("""
                            INSERT INTO lancamentos 
                            (empresa_id, operacao_id, competencia, data_lancamento, valor_base, valor_pis, valor_cofins, historico, origem_retroativa, competencia_origem, usuario_registro, status_auditoria, motivo_alteracao) 
                            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, 'ATIVO', %s)""", 
                            (reg_antigo['empresa_id'], reg_antigo['operacao_id'], reg_antigo['competencia'], reg_antigo['data_lancamento'], novo_valor_base, novo_vp, novo_vc, reg_antigo['historico'], reg_antigo['origem_retroativa'], reg_antigo['competencia_origem'], st.session_state.usuario_logado, f"Nova versão do ID {id_retificar}"))
                        
                        conn.commit()
                        st.success("Retificação concluída com sucesso! Histórico preservado.")
                        st.rerun()
    except:
        pass
    conn.close()

# --- 5. PARÂMETROS CONTÁBEIS E RESET ---
def resetar_tabelas_apuracao():
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("DROP TABLE IF EXISTS lancamentos")
    cursor.execute("DROP TABLE IF EXISTS operacoes")
    
    cursor.execute("""
        CREATE TABLE operacoes (
            id INT AUTO_INCREMENT PRIMARY KEY,
            nome VARCHAR(100) NOT NULL,
            tipo ENUM('RECEITA', 'DESPESA') NOT NULL,
            gera_credito BOOLEAN DEFAULT FALSE,
            conta_debito VARCHAR(50),
            conta_credito VARCHAR(50),
            historico_padrao VARCHAR(255)
        )
    """)
    
    cursor.execute("""
        CREATE TABLE lancamentos (
            id INT AUTO_INCREMENT PRIMARY KEY,
            empresa_id INT NOT NULL,
            operacao_id INT NOT NULL,
            competencia VARCHAR(7) NOT NULL,
            data_lancamento DATE NOT NULL,
            valor_base DECIMAL(15,2) NOT NULL,
            valor_pis DECIMAL(15,2) NOT NULL,
            valor_cofins DECIMAL(15,2) NOT NULL,
            historico TEXT,
            origem_retroativa BOOLEAN DEFAULT FALSE,
            competencia_origem VARCHAR(7),
            usuario_registro VARCHAR(100),
            status_auditoria ENUM('ATIVO', 'INATIVO') DEFAULT 'ATIVO',
            motivo_alteracao VARCHAR(255) DEFAULT NULL,
            data_alteracao TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP
        )
    """)
    
    operacoes_padrao = [
        ("Venda de Mercadorias / Produtos", "RECEITA", False, "3.1.1.01", "2.1.2.05", "Venda de Mercadoria Ref"),
        ("Venda de Serviços", "RECEITA", False, "3.1.1.02", "2.1.2.05", "Prestacao de Servico Ref"),
        ("Receita Financeira", "RECEITA", False, "3.2.1.01", "2.1.2.05", "Receita Financeira Ref"),
        ("Outras Receitas Operacionais", "RECEITA", False, "3.2.1.99", "2.1.2.05", "Outras Receitas Ref"),
        ("Compra de Mercadorias (Revenda)", "DESPESA", True, "1.1.3.01", "2.1.1.01", "Compra Mercadoria Ref"),
        ("Compra de Insumos", "DESPESA", True, "1.1.3.01", "2.1.1.01", "Compra Insumos Ref"),
        ("Energia Elétrica Térmica", "DESPESA", True, "1.1.3.01", "2.1.1.01", "Energia Eletrica Ref"),
        ("Aluguéis Pagos", "DESPESA", True, "1.1.3.01", "2.1.1.01", "Alugueis Pagos Ref"),
        ("Depreciação Ativo Imobilizado", "DESPESA", True, "1.1.3.01", "2.1.1.01", "Depreciacao Ativo Ref"),
        ("Frete na Aquisição", "DESPESA", True, "1.1.3.01", "2.1.1.01", "Frete Aquisicao Ref"),
        ("Frete na Venda", "DESPESA", True, "1.1.3.01", "2.1.1.01", "Frete Venda Ref"),
        ("Devolução de Vendas", "DESPESA", True, "1.1.3.01", "2.1.1.01", "Devolucao Venda Ref")
    ]
    cursor.executemany("INSERT INTO operacoes (nome, tipo, gera_credito, conta_debito, conta_credito, historico_padrao) VALUES (%s, %s, %s, %s, %s, %s)", operacoes_padrao)
    conn.commit()
    conn.close()

def modulo_parametros():
    st.markdown("## ⚙️ Parâmetros Contábeis")
    st.write("Área exclusiva para Administradores gerenciarem as bases do sistema.")
    
    tab_nova_op, tab_reset = st.tabs(["➕ Cadastrar Nova Operação", "🚨 Manutenção do Sistema"])
    
    with tab_nova_op:
        with st.form("form_nova_op", clear_on_submit=True):
            st.markdown("#### Criar Natureza de Operação")
            nome_op = st.text_input("Nome da Operação", placeholder="Ex: Compra de Imobilizado...")
            
            c1, c2 = st.columns(2)
            conta_deb = c1.text_input("Conta Débito (ERP)", placeholder="Ex: 1.1.3.01")
            conta_cred = c2.text_input("Conta Crédito (ERP)", placeholder="Ex: 2.1.1.01")
            
            hist_padrao = st.text_input("Histórico Padrão (Sem a data)", placeholder="Ex: Aquisicao de Imobilizado Ref")
            
            tipo_natureza = st.radio("Natureza da Operação no PIS/COFINS:", ["Despesa (Gera Crédito/Direito)", "Receita (Gera Débito/Obrigação)"])
            
            if st.form_submit_button("Salvar Operação no Banco"):
                conn = get_db_connection()
                cursor = conn.cursor()
                tipo_bd = 'DESPESA' if 'Despesa' in tipo_natureza else 'RECEITA'
                gera_cred = True if 'Despesa' in tipo_natureza else False
                
                cursor.execute("INSERT INTO operacoes (nome, tipo, gera_credito, conta_debito, conta_credito, historico_padrao) VALUES (%s, %s, %s, %s, %s, %s)", 
                               (nome_op, tipo_bd, gera_cred, conta_deb, conta_cred, hist_padrao))
                conn.commit()
                conn.close()
                st.success("Operação adicionada com sucesso!")

    with tab_reset:
        st.error("Atenção: Esta ação apaga todas as apurações e recria a base de operações.")
        frase = st.text_input("Digite a frase de segurança: CONFIRMAR EXCLUSAO TOTAL")
        if st.button("Executar Reset do Banco"):
            if frase == "CONFIRMAR EXCLUSAO TOTAL":
                resetar_tabelas_apuracao()
                st.success("Banco formatado e recarregado com sucesso.")
            else:
                st.warning("Frase de segurança incorreta.")

# --- 6. EXPORTAÇÃO ERP (RELATÓRIOS) ---
def modulo_relatorios():
    st.markdown("## 📄 Relatórios & Integração ERP")
    conn = get_db_connection()
    try:
        df_empresas = pd.read_sql("SELECT id, nome, cnpj FROM empresas", conn)
    except:
        conn.close(); return
        
    c1, c2 = st.columns([3, 1])
    emp_sel = c1.selectbox("Empresa", df_empresas.apply(lambda row: f"{row['nome']} - {row['cnpj']}", axis=1))
    emp_id = int(df_empresas.loc[df_empresas.apply(lambda row: f"{row['nome']} - {row['cnpj']}", axis=1) == emp_sel].iloc[0]['id'])
    competencia = c2.text_input("Competência", value=competencia_padrao)
    
    try:
        m, a = competencia.split('/'); comp_db = f"{a}-{m.zfill(2)}"
        query = f"""
            SELECT l.data_lancamento, o.nome as operacao, o.conta_debito, o.conta_credito, o.historico_padrao,
            l.valor_base, l.valor_pis, l.valor_cofins 
            FROM lancamentos l JOIN operacoes o ON l.operacao_id = o.id
            WHERE l.empresa_id = {emp_id} AND l.competencia = '{comp_db}' AND l.status_auditoria = 'ATIVO'
        """
        df_dados = pd.read_sql(query, conn)
    except:
        df_dados = pd.DataFrame()
    conn.close()

    if not df_dados.empty:
        st.success("Integração Contábil ERP (XLSX)")
        ultimo_dia = calendar.monthrange(int(a), int(m))[1]
        data_lancamento_erp = f"{ultimo_dia:02d}/{m.zfill(2)}/{a}"

        linhas_erp = []
        for _, row in df_dados.iterrows():
            hist_final = f"{row['historico_padrao']} - {competencia}" 
            
            if row['valor_pis'] > 0:
                linhas_erp.append({'Lancto Aut.': '', 'Debito': row['conta_debito'], 'Credito': row['conta_credito'], 'Data': data_lancamento_erp, 'Valor': row['valor_pis'], 'Cod. Historico': '', 'Historico': f"PIS: {hist_final}", 'Ccusto Debito': '', 'Ccusto Credito': '', 'Nr.Documento': '', 'Complemento': ''})
            
            if row['valor_cofins'] > 0:
                linhas_erp.append({'Lancto Aut.': '', 'Debito': row['conta_debito'], 'Credito': row['conta_credito'], 'Data': data_lancamento_erp, 'Valor': row['valor_cofins'], 'Cod. Historico': '', 'Historico': f"COFINS: {hist_final}", 'Ccusto Debito': '', 'Ccusto Credito': '', 'Nr.Documento': '', 'Complemento': ''})
        
        df_export = pd.DataFrame(linhas_erp, columns=['Lancto Aut.', 'Debito', 'Credito', 'Data', 'Valor', 'Cod. Historico', 'Historico', 'Ccusto Debito', 'Ccusto Credito', 'Nr.Documento', 'Complemento'])
        
        buffer = io.BytesIO()
        with pd.ExcelWriter(buffer, engine='openpyxl') as writer:
            df_export.to_excel(writer, index=False, sheet_name='Planilha1')
        
        st.download_button("📥 Baixar Arquivo ERP (Excel XLSX)", data=buffer.getvalue(), file_name=f"Exportacao_ERP_{comp_db}.xlsx", mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet", use_container_width=True)
    else:
        st.info("Nenhum lançamento ativo encontrado para esta competência.")

# --- 7. NAVEGAÇÃO LATERAL ---
with st.sidebar:
    if os.path.exists("image_b8c586.png"):
        st.image("image_b8c586.png", width=160)
    else:
        st.markdown("<h2 style='color: #004b87; text-align: center;'>🛡️ CRESCERE</h2>", unsafe_allow_html=True)
        
    st.markdown(f"<p style='text-align: center; color: #64748b;'>👤 Operador: <b>{st.session_state.usuario_logado}</b></p>", unsafe_allow_html=True)
    
    st.markdown('''
        <a href="https://conciliador-contabil-hsppms6xpbjstvmmfktgkc.streamlit.app/" target="_blank" 
           style="display: block; padding: 12px; background-color: #004b87; color: white; 
                  text-align: center; border-radius: 6px; text-decoration: none; 
                  font-weight: bold; margin-bottom: 15px; border: 2px solid #003366;">
            🚀 Conciliador Contábil
        </a>
    ''', unsafe_allow_html=True)

    st.write("---")
    menu = st.radio("Módulos do Sistema", ["Gestão de Empresas", "Apuração Mensal", "Relatórios e Integração", "⚙️ Parâmetros Contábeis"])

if menu == "Gestão de Empresas":
    modulo_empresas()
elif menu == "Apuração Mensal":
    modulo_apuracao()
elif menu == "Relatórios e Integração":
    modulo_relatorios()
elif menu == "⚙️ Parâmetros Contábeis":
    modulo_parametros()
