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
setores_vinculo = ["Nenhum"] + setores_lista
# Data mínima para permitir contratos antigos (1970)
MIN_DATE = datetime.date(1970, 1, 1)

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
                st.success(f"Conectado: {selecionado} ({user['setor']})")
                sub1, sub2 = st.tabs(["📝 Nova Solicitação", "🔄 Status e Reagendamento"])
                
                with sub1:
                    # MENSAGEM ESTRATÉGICA SOLICITADA
                    st.info("💡 **Regra Importante:** As férias podem ser divididas em até 3 períodos, sendo que um deles deve ter, obrigatoriamente, no mínimo 14 dias, e nenhum pode ser inferior a 5 dias.")
                    
                    with st.form("form_nova"):
                        c1, c2 = st.columns(2)
                        d_ini = c1.date_input("Início")
                        d_fim = c2.date_input("Fim")
                        abono = st.checkbox("Abono Pecuniário (Vender 10 dias)")
                        
                        if st.form_submit_button("Enviar Solicitação"):
                            dias = (d_fim - d_ini).days + 1
                            if dias < 5:
                                st.error("O período mínimo permitido é de 5 dias.")
                            else:
                                sql_sol = f"""INSERT INTO rh_movimentacao_ferias 
                                (id_funcionario, data_inicio, data_fim, dias_corridos, abono_pecuniario, status, ip_registro) 
                                VALUES ({user['id_funcionario']}, '{d_ini}', '{d_fim}', {dias}, {1 if abono else 0}, 'Pendente', '{current_ip}')"""
                                query_banco(sql_sol)
                                st.success("Solicitação enviada com sucesso!")

                with sub2:
                    st.write("### Seus Agendamentos")
                    historico = query_banco(f"SELECT data_inicio, data_fim, dias_corridos, status, motivo_recusa FROM rh_movimentacao_ferias WHERE id_funcionario={user['id_funcionario']} ORDER BY data_inicio DESC")
                    if historico:
                        st.dataframe(pd.DataFrame(historico), use_container_width=True)

# --- 4. ÁREA RESTRITA (LÍDER) ---
with tab_lider:
    c_l1, c_l2 = st.columns(2)
    senha = c_l1.text_input("Senha:", type="password")
    setor_trabalho = c_l2.selectbox("Setor:", setores_lista)
    
    if senha == "123":
        funcs_setor = [f for f in funcionarios_db if f['setor'] == setor_trabalho]
        st.sidebar.markdown(f"### 🏢 Gestão: {setor_trabalho}")
        menu = st.sidebar.radio("Navegação:", ["Aprovações", "Dossiê Estratégico", "Gestão de Equipe", "⚙️ Configurações"])

        # 4.2 DOSSIÊ ESTRATÉGICO (PILARES DE INTELIGÊNCIA)
        if menu == "Dossiê Estratégico":
            st.subheader(f"📊 Inteligência de Prazos - {setor_trabalho}")
            
            hoje = datetime.date.today()
            for f in funcs_setor:
                # Lógica: Se houver saldo retroativo, ele dita o prazo crítico. Senão, calcula pela admissão.
                if f['saldo_retroativo'] > 0 and f['vencimento_retroativo']:
                    limite = f['vencimento_retroativo']
                    origem = "Saldo de Implantação"
                else:
                    # Calcula o ciclo atual baseado na admissão
                    anos_empresa = (hoje.year - f['data_admissao'].year)
                    proximo_aniv = f['data_admissao'].replace(year=hoje.year)
                    if proximo_aniv < hoje: proximo_aniv = f['data_admissao'].replace(year=hoje.year + 1)
                    limite = proximo_aniv + datetime.timedelta(days=330) # 11 meses de prazo
                    origem = "Ciclo Automático"

                dias_rest = (limite - hoje).days
                cor = "🔴" if dias_rest < 90 else "🟡" if dias_rest < 180 else "🟢"
                
                with st.expander(f"{cor} {f['nome']} | Prazo: {limite.strftime('%d/%m/%Y')} ({origem})"):
                    st.write(f"**Data de Admissão:** {f['data_admissao'].strftime('%d/%m/%Y')}")
                    if f['saldo_retroativo'] > 0:
                        st.warning(f"Possui {f['saldo_retroativo']} dias pendentes de períodos anteriores.")
                    
                    hist = query_banco(f"SELECT data_inicio, data_fim, status FROM rh_movimentacao_ferias WHERE id_funcionario={f['id_funcionario']} ORDER BY data_inicio DESC")
                    if hist: st.table(pd.DataFrame(hist))

        # 4.3 GESTÃO DE EQUIPE (CORREÇÃO DE DATAS ANTIGAS)
        elif menu == "Gestão de Equipe":
            with st.expander("➕ Admitir Novo Colaborador"):
                with st.form("add_func", clear_on_submit=True):
                    n_n = st.text_input("Nome")
                    e_n = st.text_input("E-mail")
                    # min_value=MIN_DATE permite acessar anos como 2001 e 2003
                    adm = st.date_input("Data de Admissão", min_value=MIN_DATE)
                    
                    st.markdown("---")
                    st.caption("Saldo de Implantação (Para quem já possui períodos vencidos/acumulados)")
                    c_s1, c_s2 = st.columns(2)
                    s_r = c_s1.number_input("Dias Pendentes do Passado", min_value=0, value=0)
                    v_r = c_s2.date_input("Data Limite para tirar esse saldo", min_value=MIN_DATE)
                    
                    if st.form_submit_button("Finalizar Cadastro"):
                        sql = f"""INSERT INTO rh_funcionarios (nome, email_corporativo, data_admissao, saldo_retroativo, vencimento_retroativo, setor, is_ativo) 
                                 VALUES ('{n_n.replace("'", "''")}', '{e_n}', '{adm}', {s_r}, '{v_r}', '{setor_trabalho}', 1)"""
                        query_banco(sql)
                        st.success("Cadastrado com sucesso!")
                        st.rerun()

            st.write("### Equipe Atual")
            for f in funcs_setor:
                with st.container():
                    c1, c2, c3, c4 = st.columns([3, 1, 1, 1])
                    c1.write(f"**{f['nome']}**\n\nAdmissão: {f['data_admissao'].strftime('%d/%m/%Y')}")
                    
                    if c2.button("📝 Editar", key=f"ed_{f['id_funcionario']}"):
                        st.session_state[f"editing_{f['id_funcionario']}"] = True
                    
                    if st.session_state.get(f"editing_{f['id_funcionario']}"):
                        with st.form(f"form_ed_{f['id_funcionario']}"):
                            en = st.text_input("Nome", value=f['nome'])
                            em = st.text_input("E-mail", value=f['email_corporativo'])
                            ea = st.date_input("Admissão", value=f['data_admissao'], min_value=MIN_DATE)
                            
                            st.caption("Saldo de Implantação")
                            col_s1, col_s2 = st.columns(2)
                            esr = col_s1.number_input("Dias Pendentes", value=f['saldo_retroativo'])
                            evr = col_e2.date_input("Nova Data Limite", value=f['vencimento_retroativo'] or datetime.date.today(), min_value=MIN_DATE)
                            
                            if st.form_submit_button("Salvar Alterações"):
                                query_banco(f"UPDATE rh_funcionarios SET nome='{en.replace('\'', '\'\'')}', email_corporativo='{em}', data_admissao='{ea}', saldo_retroativo={esr}, vencimento_retroativo='{evr}' WHERE id_funcionario={f['id_funcionario']}")
                                del st.session_state[f"editing_{f['id_funcionario']}"]
                                st.rerun()
                    
                    if c3.button("🔄 Reset IP", key=f"rs_{f['id_funcionario']}"):
                        query_banco(f"UPDATE rh_funcionarios SET ip_maquina=NULL WHERE id_funcionario={f['id_funcionario']}")
                        st.rerun()
                    
                    if c4.button("🗑️", key=f"del_{f['id_funcionario']}"):
                        query_banco(f"DELETE FROM rh_funcionarios WHERE id_funcionario={f['id_funcionario']}")
                        st.rerun()
                st.divider()

        # Menu Aprovações e Configurações seguem a lógica anterior...
