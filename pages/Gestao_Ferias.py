import streamlit as st
import sys
import os
import datetime
import pandas as pd

# 1. AJUSTE DE INFRAESTRUTURA
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

try:
    from database import query_banco
    from mailer import enviar_email
except Exception as e:
    st.error(f"Erro de importação: {e}")
    st.stop()

st.set_page_config(page_title="Gestão de Férias", layout="wide", page_icon="📅")

def get_remote_ip():
    try:
        return st.context.headers.get("X-Forwarded-For", "127.0.0.1").split(',')[0]
    except:
        return "127.0.0.1"

current_ip = get_remote_ip()
setores_lista = ["CONTÁBIL", "D.P", "FISCAL"]

st.title("📅 Sistema Estratégico de Férias")

# 2. CARREGAMENTO DE DADOS
try:
    funcionarios_db = query_banco("SELECT * FROM rh_funcionarios WHERE is_ativo = 1 ORDER BY nome ASC")
except Exception as e:
    st.error(f"Erro ao conectar com o banco: {e}")
    st.stop()

tab_func, tab_lider = st.tabs(["👤 Espaço do Funcionário", "🔒 Área Restrita (Líder)"])

# --- 3. ESPAÇO DO FUNCIONÁRIO ---
with tab_func:
    if not funcionarios_db:
        st.info("Nenhum funcionário cadastrado.")
    else:
        nomes = [f['nome'] for f in funcionarios_db]
        selecionado = st.selectbox("Selecione seu nome:", [""] + nomes, key="sel_func")

        if selecionado:
            user = next(item for item in funcionarios_db if item["nome"] == selecionado)
            
            if not user['ip_maquina']:
                st.warning("⚠️ PRIMEIRO ACESSO: Vincule sua máquina.")
                if st.button("Confirmar e Vincular Máquina"):
                    query_banco(f"UPDATE rh_funcionarios SET ip_maquina='{current_ip}' WHERE id_funcionario={user['id_funcionario']}")
                    st.rerun()
            elif user['ip_maquina'] != current_ip:
                st.error("🚫 Acesso Negado: Estação de trabalho não autorizada.")
            else:
                st.success(f"Conectado: {selecionado}")
                sub1, sub2 = st.tabs(["📝 Nova Solicitação", "🔄 Status e Histórico"])
                
                with sub1:
                    with st.form("form_nova"):
                        c1, c2 = st.columns(2)
                        d_ini = c1.date_input("Início")
                        d_fim = c2.date_input("Fim")
                        abono = st.checkbox("Abono (Vender 10 dias)")
                        if st.form_submit_button("Enviar Solicitação"):
                            dias = (d_fim - d_ini).days + 1
                            if dias < 5:
                                st.error("Mínimo de 5 dias.")
                            else:
                                sql_sol = f"""INSERT INTO rh_movimentacao_ferias 
                                (id_funcionario, data_inicio, data_fim, dias_corridos, abono_pecuniario, status, ip_registro) 
                                VALUES ({user['id_funcionario']}, '{d_ini}', '{d_fim}', {dias}, {1 if abono else 0}, 'Pendente', '{current_ip}')"""
                                query_banco(sql_sol)
                                st.success("Solicitação enviada!")

                with sub2:
                    hist_p = query_banco(f"SELECT data_inicio, data_fim, status, motivo_recusa FROM rh_movimentacao_ferias WHERE id_funcionario={user['id_funcionario']} ORDER BY data_inicio DESC")
                    if hist_p: st.dataframe(pd.DataFrame(hist_p), use_container_width=True)

# --- 4. ÁREA RESTRITA (LÍDER) ---
with tab_lider:
    c_l1, c_l2 = st.columns(2)
    senha = c_l1.text_input("Senha:", type="password")
    setor_trabalho = c_l2.selectbox("Setor:", setores_lista)
    
    if senha == "123":
        funcs_setor = [f for f in funcionarios_db if f['setor'] == setor_trabalho]
        st.sidebar.markdown(f"### 🏢 Setor: {setor_trabalho}")
        menu = st.sidebar.radio("Navegação:", ["Aprovações", "Dossiê Estratégico", "Gestão de Equipe"])

        if menu == "Aprovações":
            pendentes = query_banco(f"""SELECT f.nome, f.email_corporativo, m.* FROM rh_movimentacao_ferias m 
                                       JOIN rh_funcionarios f ON m.id_funcionario = f.id_funcionario 
                                       WHERE m.status IN ('Pendente', 'Reagendamento Solicitado') AND f.setor = '{setor_trabalho}'""")
            if not pendentes: st.info("Nada pendente.")
            for p in pendentes:
                with st.expander(f"{p['nome']} - {p['dias_corridos']} dias"):
                    ca, cr = st.columns(2)
                    if ca.button("✅ Aprovar", key=f"ap_{p['id_movimento']}"):
                        query_banco(f"UPDATE rh_movimentacao_ferias SET status='Aprovado' WHERE id_movimento={p['id_movimento']}")
                        config = query_banco(f"SELECT * FROM rh_configuracoes_setores WHERE setor = '{setor_trabalho}'")
                        if config:
                            c = config[0]
                            periodo = f"{p['data_inicio'].strftime('%d/%m/%Y')} a {p['data_fim'].strftime('%d/%m/%Y')}"
                            enviar_email(p['email_corporativo'], "Férias Aprovadas", f"Suas férias de {periodo} foram aprovadas.")
                            enviar_email(c['email_rh'], "Aviso RH", f"{p['nome']} sairá de {periodo}.")
                            if c['setor_vinculado']:
                                v = query_banco(f"SELECT email_lider FROM rh_configuracoes_setores WHERE setor = '{c['setor_vinculado']}'")
                                if v: enviar_email(v[0]['email_lider'], "Aviso Escala", f"{p['nome']} estará fora de {periodo}.")
                        st.success("Aprovado e Notificado!")
                        st.rerun()

        elif menu == "Dossiê Estratégico":
            for f in funcs_setor:
                limite = f['data_admissao'] + datetime.timedelta(days=700)
                dias_rest = (limite - datetime.date.today()).days
                with st.expander(f"{f['nome']}"):
                    st.write(f"Limite: {limite.strftime('%d/%m/%Y')} ({dias_rest // 30} meses)")
                    hist = query_banco(f"SELECT data_inicio, data_fim, status FROM rh_movimentacao_ferias WHERE id_funcionario={f['id_funcionario']} ORDER BY data_inicio DESC")
                    if hist: st.table(pd.DataFrame(hist))

        elif menu == "Gestão de Equipe":
            with st.expander("➕ Admitir Novo Colaborador"):
                with st.form("add_func", clear_on_submit=True):
                    n_n = st.text_input("Nome")
                    e_n = st.text_input("E-mail")
                    a_n = st.date_input("Admissão")
                    
                    if st.form_submit_button("Finalizar"):
                        # 1. Tratamento seguro separado (evita o erro de aspas do Python/MySQL)
                        nome_seguro = n_n.replace("'", "''")
                        data_segura = a_n.strftime('%Y-%m-%d')
                        
                        # 2. Montagem da Query limpa
                        sql = f"INSERT INTO rh_funcionarios (nome, email_corporativo, data_admissao, setor, is_ativo) VALUES ('{nome_seguro}', '{e_n}', '{data_segura}', '{setor_trabalho}', 1)"
                        
                        # 3. Tratamento de erro explícito
                        try:
                            query_banco(sql)
                            st.success("Cadastrado com sucesso!")
                            st.rerun()
                        except Exception as erro_real:
                            st.error(f"ERRO EXATO DO BANCO: {erro_real}")
            
            # Listagem da Equipe
            st.write("### Equipe Atual")
            for f in funcs_setor:
                c1, c2 = st.columns([4, 1])
                c1.write(f"**{f['nome']}** - {f['email_corporativo']}")
                if c2.button("Reset IP", key=f"rs_{f['id_funcionario']}"):
                    query_banco(f"UPDATE rh_funcionarios SET ip_maquina=NULL WHERE id_funcionario={f['id_funcionario']}")
                    st.rerun()
                    
