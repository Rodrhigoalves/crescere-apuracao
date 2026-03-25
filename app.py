import streamlit as st
import mysql.connector
import pandas as pd
import requests
from datetime import date, timedelta
import io
import bcrypt
from fpdf import FPDF # Requer: pip install fpdf

# --- 1. CONFIGURAÇÕES VISUAIS E INJEÇÃO CSS ---
st.set_page_config(page_title="Crescere - Apuração Fiscal", layout="wide", initial_sidebar_state="expanded")

st.markdown("""
<style>
    /* Identidade Visual Corporativa */
    .stApp { background-color: #f4f6f9; }
    
    /* Botões padronizados e simétricos */
    .stButton>button { 
        background-color: #004b87; 
        color: white; 
        border-radius: 4px; 
        border: none; 
        font-weight: 500; 
        height: 45px;
        transition: all 0.2s ease-in-out;
    }
    .stButton>button:hover { background-color: #003366; color: white; transform: translateY(-1px); box-shadow: 0 4px 6px rgba(0,0,0,0.1); }
    
    /* Containers e Formulários */
    div[data-testid="stForm"], .css-1d391kg, .stExpander, div[data-testid="stVerticalBlock"] > div > div[data-testid="stVerticalBlock"] { 
        background-color: #ffffff; 
        padding: 20px; 
        border-radius: 8px; 
        box-shadow: 0 1px 3px rgba(0,0,0,0.05); 
        border: 1px solid #e2e8f0;
    }
    
    h1, h2, h3, h4 { color: #0f172a; font-weight: 600; font-family: 'Segoe UI', Tahoma, Geneva, Verdana, sans-serif; }
    .stTextInput input, .stNumberInput input, .stSelectbox div[data-baseweb="select"] { background-color: #f8fafc; border: 1px solid #cbd5e1; }
    
    /* Remoção de elementos visuais desnecessários */
    #MainMenu {visibility: hidden;}
    footer {visibility: hidden;}
</style>
""", unsafe_allow_html=True)

# --- 2. MOTOR DE BANCO DE DADOS E FUNÇÕES BASE ---
def get_db_connection():
    try:
        return mysql.connector.connect(**st.secrets["mysql"])
    except mysql.connector.Error as err:
        st.error(f"Erro crítico de conexão com o banco de dados: {err}")
        st.stop()

def verificar_senha(senha_plana, hash_banco):
    return bcrypt.checkpw(senha_plana.encode('utf-8'), hash_banco.encode('utf-8'))

def formatar_moeda(valor):
    return f"R$ {valor:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")

def consultar_cnpj(cnpj_limpo):
    url = f"https://receitaws.com.br/v1/cnpj/{cnpj_limpo}"
    try:
        response = requests.get(url, timeout=10)
        return response.json() if response.status_code == 200 else None
    except requests.RequestException: 
        return None

# --- 3. MOTOR DE CÁLCULO TRIBUTÁRIO ---
def calcular_impostos(regime, operacao_nome, valor_base):
    """Retorna uma tupla (valor_pis, valor_cofins) baseada nas regras de negócio."""
    if regime == "Lucro Real":
        if "Receita Financeira" in operacao_nome:
            return (valor_base * 0.0065, valor_base * 0.04)
        return (valor_base * 0.0165, valor_base * 0.076)
    elif regime == "Lucro Presumido":
        return (valor_base * 0.0065, valor_base * 0.03)
    return (0.0, 0.0)

# --- 4. CONTROLE DE ESTADO E AUTENTICAÇÃO ---
if 'autenticado' not in st.session_state: st.session_state.autenticado = False
if 'dados_form' not in st.session_state: st.session_state.dados_form = {"id": None, "nome": "", "fantasia": "", "cnpj": "", "regime": "Lucro Real", "tipo": "Matriz", "cnae": "", "endereco": ""}
if 'rascunho_lancamentos' not in st.session_state: st.session_state.rascunho_lancamentos = []

hoje = date.today()
competencia_padrao = (hoje.replace(day=1) - timedelta(days=1)).strftime("%m/%Y")

if not st.session_state.autenticado:
    st.markdown("<br><br><br>", unsafe_allow_html=True)
    _, login_col, _ = st.columns([1, 1.5, 1])
    with login_col:
        st.markdown("<h2 style='text-align: center; color: #004b87;'>CRESCERE</h2>", unsafe_allow_html=True)
        st.markdown("<p style='text-align: center; color: #64748b;'>Autenticação de Usuário</p>", unsafe_allow_html=True)
        with st.form("form_login"):
            user_input = st.text_input("Usuário")
            pw_input = st.text_input("Senha", type="password")
            if st.form_submit_button("Entrar no Sistema", use_container_width=True):
                conn = get_db_connection()
                cursor = conn.cursor(dictionary=True)
                try:
                    cursor.execute("SELECT u.* FROM usuarios u WHERE u.username = %s AND u.status_usuario = 'ATIVO'", (user_input,))
                    user_data = cursor.fetchone()
                    if user_data and verificar_senha(pw_input, user_data['senha_hash']):
                        st.session_state.autenticado = True
                        st.session_state.usuario_logado = user_data['nome']
                        st.session_state.username = user_data['username']
                        # Garantindo que rodrhigo seja sempre SUPER_ADMIN conforme regra
                        st.session_state.nivel_acesso = "SUPER_ADMIN" if user_data['username'].lower() == "rodrhigo" else user_data['nivel_acesso']
                        st.rerun()
                    else: 
                        st.error("Credenciais inválidas ou usuário inativo.")
                except Exception as e:
                    st.error(f"Erro na autenticação: {e}")
                finally:
                    conn.close()
    st.stop()

# --- 5. MÓDULO GESTÃO DE EMPRESAS ---
def modulo_empresas():
    st.markdown("### Gestão de Empresas e Clientes")
    tab_cad, tab_lista = st.tabs(["Novo Cadastro", "Unidades Cadastradas"])
    
    with tab_cad:
        c_busca, c_btn = st.columns([3, 1])
        with c_busca: cnpj_input = st.text_input("CNPJ para busca automática na Receita Federal:")
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
                else:
                    st.warning("CNPJ não encontrado ou inválido.")
        st.divider()
        
        f = st.session_state.dados_form
        with st.form("form_empresa"):
            c1, c2 = st.columns(2)
            nome = c1.text_input("Razão Social", value=f['nome'])
            fanta = c2.text_input("Nome Fantasia", value=f['fantasia'])
            
            c3, c4, c5 = st.columns([2, 1.5, 1.5])
            cnpj = c3.text_input("CNPJ", value=f['cnpj'])
            regime = c4.selectbox("Regime Tributário", ["Lucro Real", "Lucro Presumido"], index=0 if f['regime'] == "Lucro Real" else 1)
            tipo = c5.selectbox("Tipo de Unidade", ["Matriz", "Filial"], index=0 if f['tipo'] == "Matriz" else 1)
            
            c6, c7 = st.columns([1, 3])
            cnae = c6.text_input("CNAE Principal", value=f['cnae'])
            endereco = c7.text_input("Endereço Completo", value=f['endereco'])
            
            if st.form_submit_button("Salvar Registro da Empresa", use_container_width=True):
                if not nome or not cnpj:
                    st.error("Razão Social e CNPJ são obrigatórios.")
                else:
                    conn = get_db_connection()
                    cursor = conn.cursor()
                    try:
                        if f['id']: 
                            cursor.execute("UPDATE empresas SET nome=%s, fantasia=%s, cnpj=%s, regime=%s, tipo=%s, cnae=%s, endereco=%s WHERE id=%s", 
                                           (nome, fanta, cnpj, regime, tipo, cnae, endereco, f['id']))
                        else: 
                            cursor.execute("INSERT INTO empresas (nome, fantasia, cnpj, regime, tipo, cnae, endereco, status_assinatura) VALUES (%s,%s,%s,%s,%s,%s,%s,'ATIVO')", 
                                           (nome, fanta, cnpj, regime, tipo, cnae, endereco))
                        conn.commit()
                        st.session_state.dados_form = {"id": None, "nome": "", "fantasia": "", "cnpj": "", "regime": "Lucro Real", "tipo": "Matriz", "cnae": "", "endereco": ""}
                        st.success("Dados da empresa gravados com sucesso!")
                    except Exception as e:
                        conn.rollback()
                        st.error(f"Erro ao salvar: {e}")
                    finally:
                        conn.close()

    with tab_lista:
        conn = get_db_connection()
        try:
            df = pd.read_sql("SELECT id, nome, cnpj, regime, tipo, status_assinatura FROM empresas", conn)
            for _, row in df.iterrows():
                col_info, col_btn = st.columns([5, 1])
                status_color = "green" if row['status_assinatura'] == 'ATIVO' else "red"
                col_info.markdown(f"**{row['nome']}** ({row['tipo']}) - <span style='color:{status_color}; font-size:12px;'>{row['status_assinatura']}</span><br><small>CNPJ: {row['cnpj']} | Regime: {row['regime']}</small>", unsafe_allow_html=True)
                if col_btn.button("Editar", key=f"btn_emp_{row['id']}"):
                    df_edit = pd.read_sql(f"SELECT * FROM empresas WHERE id={row['id']}", conn)
                    st.session_state.dados_form = df_edit.iloc[0].to_dict()
                    st.rerun()
                st.divider()
        except Exception as e:
            st.error(f"Erro ao carregar lista: {e}")
        finally:
            conn.close()

# --- 6. MÓDULO APURAÇÃO (ALINHAMENTO E AUDITORIA SAP) ---
def modulo_apuracao():
    st.markdown("### Apuração de Impostos (PIS/COFINS)")
    conn = get_db_connection()
    try:
        df_emp = pd.read_sql("SELECT id, nome, cnpj, regime FROM empresas WHERE status_assinatura = 'ATIVO'", conn)
        df_op = pd.read_sql("SELECT * FROM operacoes ORDER BY tipo DESC, nome ASC", conn)
    except Exception as e:
        st.error(f"Erro ao carregar dados base: {e}")
        return
        
    df_op['nome_exibicao'] = df_op.apply(lambda x: f"[{'DÉBITO' if x['tipo'] == 'RECEITA' else 'CRÉDITO'}] {x['nome']}", axis=1)

    c_emp, c_comp, c_user = st.columns([2, 1, 1])
    emp_sel = c_emp.selectbox("Selecione a Empresa", df_emp.apply(lambda r: f"{r['nome']} - {r['cnpj']}", axis=1))
    emp_id = int(df_emp.loc[df_emp.apply(lambda r: f"{r['nome']} - {r['cnpj']}", axis=1) == emp_sel].iloc[0]['id'])
    regime = df_emp.loc[df_emp['id'] == emp_id].iloc[0]['regime']
    competencia = c_comp.text_input("Competência (MM/AAAA)", value=competencia_padrao)
    c_user.text_input("Operador (Audit)", value=st.session_state.usuario_logado, disabled=True)

    st.divider()
    
    # Simetria Rigorosa: Containers de mesma altura
    col_in, col_ras = st.columns([1, 1], gap="large")

    with col_in:
        st.markdown("#### Novo Lançamento")
        with st.container(height=380, border=False): # Altura fixada para alinhar perfeitamente com o Rascunho
            op_sel = st.selectbox("Classificação da Operação", df_op['nome_exibicao'].tolist())
            v_base = st.number_input("Valor da Base de Cálculo (R$)", min_value=0.00, step=100.0)
            hist = st.text_input("Histórico / Observação Livre")
            
            c_retro, c_origem = st.columns([1, 1])
            retro = c_retro.checkbox("Lançamento Retroativo")
            comp_origem = c_origem.text_input("Mês de Origem", placeholder="MM/AAAA", disabled=not retro)
            
            st.markdown("<div style='margin-top: 15px;'></div>", unsafe_allow_html=True)
            if st.button("Adicionar ao Rascunho", use_container_width=True):
                if v_base <= 0:
                    st.warning("A base de cálculo deve ser maior que zero.")
                else:
                    op_row = df_op[df_op['nome_exibicao'] == op_sel].iloc[0]
                    vp, vc = calcular_impostos(regime, op_row['nome'], v_base)
                    
                    st.session_state.rascunho_lancamentos.append({
                        "emp_id": emp_id, "op_id": int(op_row['id']), "op_nome": op_sel, 
                        "v_base": v_base, "v_pis": vp, "v_cofins": vc, 
                        "hist": hist, "retro": retro, "origem": comp_origem if retro else None
                    })
                    st.rerun()

    with col_ras:
        st.markdown("#### Rascunho de Apuração")
        with st.container(height=380, border=True): # Scroll interno limpo
            if not st.session_state.rascunho_lancamentos:
                st.markdown("<p style='text-align: center; color: #94a3b8; margin-top: 50px;'>Nenhum lançamento no rascunho.</p>", unsafe_allow_html=True)
            else:
                for i, it in enumerate(st.session_state.rascunho_lancamentos):
                    c_txt, c_val, c_del = st.columns([5, 3, 1])
                    c_txt.markdown(f"<small style='line-height: 1.2;'><b>{it['op_nome']}</b><br>PIS: {formatar_moeda(it['v_pis'])} | COF: {formatar_moeda(it['v_cofins'])}</small>", unsafe_allow_html=True)
                    c_val.markdown(f"<span style='font-size: 14px; font-weight: 600;'>{formatar_moeda(it['v_base'])}</span>", unsafe_allow_html=True)
                    if c_del.button("×", key=f"del_rasc_{i}", help="Remover item"):
                        st.session_state.rascunho_lancamentos.pop(i)
                        st.rerun()
                    st.markdown("<hr style='margin: 5px 0;'>", unsafe_allow_html=True)

        # Botão Gravar Perfeitamente Alinhado com a base do container esquerdo
        if st.button("Gravar Registros no Banco de Dados", type="primary", use_container_width=True, disabled=len(st.session_state.rascunho_lancamentos)==0):
            cursor = conn.cursor()
            try:
                m, a = competencia.split('/')
                comp_db = f"{a}-{m.zfill(2)}"
                cursor.execute("START TRANSACTION")
                
                for it in st.session_state.rascunho_lancamentos:
                    query = """INSERT INTO lancamentos 
                               (empresa_id, operacao_id, competencia, data_lancamento, valor_base, valor_pis, valor_cofins, 
                                historico, usuario_registro, status_auditoria, origem_retroativa, competencia_origem) 
                               VALUES (%s,%s,%s,CURDATE(),%s,%s,%s,%s,%s,'ATIVO',%s,%s)"""
                    valores = (it['emp_id'], it['op_id'], comp_db, it['v_base'], it['v_pis'], it['v_cofins'], 
                               it['hist'], st.session_state.username, it['retro'], it['origem'])
                    cursor.execute(query, valores)
                
                conn.commit()
                st.session_state.rascunho_lancamentos = []
                st.success("Lançamentos auditados e gravados com sucesso!")
                st.rerun()
            except Exception as e:
                conn.rollback()
                st.error(f"Erro transacional ao gravar: {e}")

    st.divider()
    
    # Extrato e Imutabilidade (Estilo SAP)
    st.markdown("#### Extrato Consolidado e Retificação de Auditoria")
    try:
        m, a = competencia.split('/')
        comp_db = f"{a}-{m.zfill(2)}"
        query_ext = f"""
            SELECT l.id as ID, o.nome as Operacao, l.valor_base, l.valor_pis, l.valor_cofins, l.historico 
            FROM lancamentos l 
            JOIN operacoes o ON l.operacao_id = o.id 
            WHERE l.empresa_id = {emp_id} AND l.competencia = '{comp_db}' AND l.status_auditoria = 'ATIVO' 
            ORDER BY l.id DESC
        """
        df_ext = pd.read_sql(query_ext, conn)
        
        if not df_ext.empty:
            df_view = df_ext.copy()
            df_view['valor_base'] = df_view['valor_base'].apply(formatar_moeda)
            df_view['valor_pis'] = df_view['valor_pis'].apply(formatar_moeda)
            df_view['valor_cofins'] = df_view['valor_cofins'].apply(formatar_moeda)
            st.dataframe(df_view, use_container_width=True, hide_index=True)
            
            with st.expander("⚠️ Retificar Lançamento (Gera Trilha de Auditoria)", expanded=False):
                st.markdown("<small>Atenção: A retificação inativará o registro original e criará um novo vínculo, preservando o histórico para auditoria.</small>", unsafe_allow_html=True)
                with st.form("form_retifica"):
                    c_id, c_nv, c_mot = st.columns([1, 2, 4])
                    id_ret = c_id.number_input("ID do Lançamento", min_value=0, step=1)
                    n_val = c_nv.number_input("Novo Valor Base Corrigido", min_value=0.0, step=50.0)
                    motivo = c_mot.text_input("Justificativa Legal/Contábil (Obrigatório)")
                    
                    if st.form_submit_button("Confirmar Retificação SAP"):
                        if not motivo.strip():
                            st.error("A justificativa é estritamente obrigatória para trilha de auditoria.")
                        elif id_ret not in df_ext['ID'].values:
                            st.error("ID não encontrado ou já está inativo nesta competência.")
                        else:
                            cursor = conn.cursor(dictionary=True)
                            try:
                                cursor.execute("START TRANSACTION")
                                
                                # 1. Busca dados do registro original
                                cursor.execute("SELECT * FROM lancamentos WHERE id = %s", (id_ret,))
                                old_data = cursor.fetchone()
                                
                                # 2. Inativa o original
                                cursor.execute("UPDATE lancamentos SET status_auditoria='INATIVO', motivo_alteracao=%s WHERE id=%s", 
                                               (f"RETIFICADO. Motivo: {motivo}", id_ret))
                                
                                # 3. Recalcula impostos para o novo valor
                                cursor.execute("SELECT nome FROM operacoes WHERE id = %s", (old_data['operacao_id'],))
                                op_name = cursor.fetchone()['nome']
                                novo_pis, novo_cofins = calcular_impostos(regime, op_name, n_val)
                                
                                # 4. Insere o novo registro ativo
                                novo_historico = f"[RETIFICA ID {id_ret}] {old_data['historico']}"
                                query_insert = """
                                    INSERT INTO lancamentos 
                                    (empresa_id, operacao_id, competencia, data_lancamento, valor_base, valor_pis, valor_cofins, 
                                     historico, usuario_registro, status_auditoria, origem_retroativa, competencia_origem) 
                                    VALUES (%s,%s,%s,CURDATE(),%s,%s,%s,%s,%s,'ATIVO',%s,%s)
                                """
                                cursor.execute(query_insert, (
                                    old_data['empresa_id'], old_data['operacao_id'], old_data['competencia'], 
                                    n_val, novo_pis, novo_cofins, novo_historico, st.session_state.username, 
                                    old_data['origem_retroativa'], old_data['competencia_origem']
                                ))
                                
                                conn.commit()
                                st.success("Retificação concluída com sucesso. Trilha de auditoria preservada!")
                                st.rerun()
                            except Exception as e:
                                conn.rollback()
                                st.error(f"Erro transacional na retificação: {e}")
        else:
            st.info("Nenhum lançamento ativo para esta competência.")
    except Exception as e:
        st.error(f"Erro ao gerar extrato: {e}")
    finally:
        conn.close()

# --- 7. MÓDULO EXPORTAÇÃO E RELATÓRIOS ---
def modulo_relatorios():
    st.markdown("### Integração ERP e Relatórios")
    conn = get_db_connection()
    df_emp = pd.read_sql("SELECT id, nome FROM empresas WHERE status_assinatura = 'ATIVO'", conn)
    
    with st.form("form_export"):
        c1, c2 = st.columns([2, 1])
        emp_sel = c1.selectbox("Selecione a Empresa", df_emp['nome'].tolist())
        emp_id = int(df_emp.loc[df_emp['nome'] == emp_sel].iloc[0]['id'])
        competencia = c2.text_input("Competência (MM/AAAA)", value=competencia_padrao)
        
        submit_export = st.form_submit_button("Gerar Arquivos de Exportação")
        
    if submit_export:
        try:
            m, a = competencia.split('/')
            comp_db = f"{a}-{m.zfill(2)}"
            
            # Busca lançamentos
            query = f"""
                SELECT l.*, o.nome as op_nome, o.conta_debito, o.conta_credito 
                FROM lancamentos l 
                JOIN operacoes o ON l.operacao_id = o.id 
                WHERE l.empresa_id = {emp_id} AND l.competencia = '{comp_db}' AND l.status_auditoria = 'ATIVO'
            """
            df_export = pd.read_sql(query, conn)
            
            if df_export.empty:
                st.warning("Nenhum dado encontrado para exportação nesta competência.")
            else:
                # Geração XLSX (Padrão 11 Colunas Alterdata)
                linhas_excel = []
                for _, row in df_export.iterrows():
                    data_str = row['data_lancamento'].strftime('%d/%m/%Y') if pd.notnull(row['data_lancamento']) else ''
                    historico_base = f"VLR REF {row['op_nome']} COMP {competencia} {row['historico']}"
                    
                    # Linha PIS
                    linhas_excel.append({
                        "Lancto Aut.": "", "Debito": row['conta_debito'], "Credito": row['conta_credito'],
                        "Data": data_str, "Valor": row['valor_pis'], "Cod. Historico": "", 
                        "Historico": f"PIS - {historico_base}", "Ccusto Debito": "", "Ccusto Credito": "",
                        "Nr.Documento": row['id'], "Complemento": ""
                    })
                    # Linha COFINS
                    linhas_excel.append({
                        "Lancto Aut.": "", "Debito": row['conta_debito'], "Credito": row['conta_credito'],
                        "Data": data_str, "Valor": row['valor_cofins'], "Cod. Historico": "", 
                        "Historico": f"COFINS - {historico_base}", "Ccusto Debito": "", "Ccusto Credito": "",
                        "Nr.Documento": row['id'], "Complemento": ""
                    })
                
                df_xlsx = pd.DataFrame(linhas_excel)
                buffer = io.BytesIO()
                with pd.ExcelWriter(buffer, engine='openpyxl') as writer:
                    df_xlsx.to_excel(writer, index=False, sheet_name='Integracao')
                buffer.seek(0)
                
                # Geração PDF Básico (FPDF)
                pdf = FPDF()
                pdf.add_page()
                pdf.set_font("Arial", 'B', 14)
                pdf.cell(190, 10, f"Resumo de Apuração - {emp_sel}", ln=True, align='C')
                pdf.set_font("Arial", '', 10)
                pdf.cell(190, 10, f"Competência: {competencia}", ln=True, align='C')
                pdf.ln(10)
                
                pdf.set_font("Arial", 'B', 10)
                pdf.cell(80, 8, "Operação", 1)
                pdf.cell(40, 8, "Base (R$)", 1)
                pdf.cell(35, 8, "PIS (R$)", 1)
                pdf.cell(35, 8, "COFINS (R$)", 1, ln=True)
                
                pdf.set_font("Arial", '', 9)
                tot_pis = tot_cofins = 0
                for _, row in df_export.iterrows():
                    pdf.cell(80, 8, str(row['op_nome'])[:35], 1)
                    pdf.cell(40, 8, f"{row['valor_base']:,.2f}", 1)
                    pdf.cell(35, 8, f"{row['valor_pis']:,.2f}", 1)
                    pdf.cell(35, 8, f"{row['valor_cofins']:,.2f}", 1, ln=True)
                    tot_pis += row['valor_pis']
                    tot_cofins += row['valor_cofins']
                
                pdf.set_font("Arial", 'B', 10)
                pdf.cell(120, 8, "TOTAIS", 1)
                pdf.cell(35, 8, f"{tot_pis:,.2f}", 1)
                pdf.cell(35, 8, f"{tot_cofins:,.2f}", 1, ln=True)
                
                pdf_bytes = pdf.output(dest='S').encode('latin1')
                
                st.success("Arquivos gerados com sucesso!")
                c_btn1, c_btn2, _ = st.columns([1, 1, 2])
                c_btn1.download_button("⬇️ Baixar XLSX (Alterdata)", data=buffer, file_name=f"LCTOS_{comp_db}.xlsx", mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
                c_btn2.download_button("⬇️ Baixar PDF (Conferência)", data=pdf_bytes, file_name=f"RESUMO_{comp_db}.pdf", mime="application/pdf")
                
        except Exception as e:
            st.error(f"Erro na geração dos arquivos: {e}")
    conn.close()

# --- 8. MENU SIDEBAR ---
with st.sidebar:
    st.markdown("<h2 style='color: #004b87; text-align: center;'>🛡️ CRESCERE</h2>", unsafe_allow_html=True)
    st.markdown(f"<p style='text-align: center;'>👤 <b>{st.session_state.usuario_logado}</b><br><small>{st.session_state.nivel_acesso}</small></p>", unsafe_allow_html=True)
    st.write("---")
    menu = st.radio("Módulos do Sistema", ["Gestão de Empresas", "Apuração Mensal", "Relatórios e Integração", "⚙️ Parâmetros Contábeis"])
    st.write("---")
    if st.button("🚪 Encerrar Sessão", use_container_width=True):
        st.session_state.autenticado = False; st.rerun()

# --- 9. RENDERIZAÇÃO DE ROTAS ---
if menu == "Gestão de Empresas": modulo_empresas()
elif menu == "Apuração Mensal": modulo_apuracao()
elif menu == "Relatórios e Integração": modulo_relatorios()
elif menu == "⚙️ Parâmetros Contábeis": 
    if st.session_state.nivel_acesso == "SUPER_ADMIN":
        st.info("Módulo de parametrização de contas contábeis e alíquotas. (Área restrita carregada).")
    else:
        st.error("Acesso negado. Requer nível SUPER_ADMIN.")
