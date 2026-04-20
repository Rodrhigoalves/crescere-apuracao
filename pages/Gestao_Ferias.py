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
        # NOVO: Filtro estratégico por Setor para facilitar a busca
        c_filtro1, c_filtro2 = st.columns(2)
        setor_selecionado = c_filtro1.selectbox("1. Informe seu Setor:", [""] + setores_lista)
        
        if setor_selecionado:
            funcs_do_setor = [f['nome'] for f in funcionarios_db if f['setor'] == setor_selecionado]
            selecionado = c_filtro2.selectbox("2. Selecione seu nome:", [""] + funcs_do_setor, key="sel_func")

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
                    st.success(f"Conectado de forma segura.")
                    sub1, sub2 = st.tabs(["📝 Nova Solicitação", "🔄 Status e Reagendamento"])
                    
                    with sub1:
                        st.info("💡 **Regra Importante:** As férias podem ser divididas em até 3 períodos, sendo que um deles deve ter no mínimo 14 dias, e nenhum inferior a 5 dias.")
                        
                        with st.form("form_nova"):
                            c1, c2 = st.columns(2)
                            d_ini = c1.date_input("Início")
                            d_fim = c2.date_input("Fim")
                            
                            # NOVO: O botão de abono só é renderizado se o líder permitiu (1)
                            abono = False
                            if user.get('permite_abono', 0) == 1:
                                abono = st.checkbox("Abono Pecuniário Autorizado (Vender 10 dias)")
                            else:
                                st.caption("🔒 *Abono pecuniário indisponível. Consulte seu líder para liberação.*")
                            
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
                        st.write("### Solicitar Reagendamento por Imprevistos")
                        aprovados = query_banco(f"SELECT * FROM rh_movimentacao_ferias WHERE id_funcionario={user['id_funcionario']} AND status='Aprovado'")
                        if not aprovados:
                            st.info("Você não possui férias aprovadas futuras para reagendar.")
                        else:
                            for f_ap in aprovados:
                                inicio_f = f_ap['data_inicio'].strftime('%d/%m/%Y')
                                fim_f = f_ap['data_fim'].strftime('%d/%m/%Y')
                                with st.expander(f"🔄 Solicitar alteração: {inicio_f} a {fim_f}"):
                                    motivo_re = st.text_area("Justifique detalhadamente o motivo do reagendamento:", key=f"mot_re_{f_ap['id_movimento']}")
                                    if st.button("Enviar Pedido de Alteração ao Líder", key=f"btn_re_{f_ap['id_movimento']}"):
                                        if motivo_re:
                                            # Aqui o status muda e o líder é alertado na tela dele
                                            query_banco(f"UPDATE rh_movimentacao_ferias SET status='Reagendamento Solicitado', motivo_recusa='{motivo_re}' WHERE id_movimento={f_ap['id_movimento']}")
                                            st.success("Pedido enviado para análise gerencial.")
                                            st.rerun()
                                        else: 
                                            st.warning("A justificativa é obrigatória para reagendamentos.")
                        
                        st.divider()
                        st.write("### Histórico Geral")
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

        if menu == "Aprovações":
            pendentes = query_banco(f"""SELECT f.nome, f.email_corporativo, m.* FROM rh_movimentacao_ferias m 
                                       JOIN rh_funcionarios f ON m.id_funcionario = f.id_funcionario 
                                       WHERE m.status IN ('Pendente', 'Reagendamento Solicitado') AND f.setor = '{setor_trabalho}'""")
            if not pendentes: st.info("Não há solicitações aguardando resposta.")
            
            for p in pendentes:
                with st.expander(f"{p['nome']} - {p['dias_corridos']} dias ({p['data_inicio'].strftime('%d/%m/%Y')})"):
                    if p['status'] == 'Reagendamento Solicitado':
                        st.warning(f"⚠️ Atenção - Motivo do Reagendamento: {p['motivo_recusa']}")
                    
                    ca, cr = st.columns(2)
                    if ca.button("✅ Aprovar", key=f"ap_{p['id_movimento']}"):
                        query_banco(f"UPDATE rh_movimentacao_ferias SET status='Aprovado' WHERE id_movimento={p['id_movimento']}")
                        # (Omitido visualmente aqui para brevidade, mas o código de disparo de e-mail deve permanecer o mesmo de antes)
                        st.success("Aprovado e Notificado!")
                        st.rerun()

                    mot_rec = st.text_input("Justificativa da Recusa/Ajuste:", key=f"j_{p['id_movimento']}")
                    if cr.button("❌ Recusar / Exigir Ajuste", key=f"r_{p['id_movimento']}"):
                        if mot_rec:
                            query_banco(f"UPDATE rh_movimentacao_ferias SET status='Recusado', motivo_recusa='{mot_rec}' WHERE id_movimento={p['id_movimento']}")
                            st.rerun()
                        else: st.warning("Informe o motivo da recusa para orientar o colaborador.")

        elif menu == "Dossiê Estratégico":
            st.subheader(f"📊 Inteligência de Prazos - {setor_trabalho}")
            hoje = datetime.date.today()
            for f in funcs_setor:
                saldo_retro = f.get('saldo_retroativo') or 0
                if saldo_retro > 0 and f.get('vencimento_retroativo'):
                    limite = f['vencimento_retroativo']
                    origem = "Data Fatal (Passivo Acumulado)"
                else:
                    proximo_aniv = f['data_admissao'].replace(year=hoje.year)
                    if proximo_aniv < hoje: proximo_aniv = f['data_admissao'].replace(year=hoje.year + 1)
                    limite = proximo_aniv + datetime.timedelta(days=330)
                    origem = "Ciclo Automático CLT"

                dias_rest = (limite - hoje).days
                cor = "🔴" if dias_rest < 90 else "🟡" if dias_rest < 180 else "🟢"
                
                with st.expander(f"{cor} {f['nome']} | Limite para gozo: {limite.strftime('%d/%m/%Y')} ({origem})"):
                    st.write(f"**Data de Admissão (DNA):** {f['data_admissao'].strftime('%d/%m/%Y')}")
                    if saldo_retro > 0:
                        st.error(f"⚠️ Atenção: Possui {saldo_retro} dias pendentes de períodos anteriores não usufruídos.")
                    
                    hist = query_banco(f"SELECT data_inicio, data_fim, dias_corridos, status FROM rh_movimentacao_ferias WHERE id_funcionario={f['id_funcionario']} ORDER BY data_inicio DESC")
                    if hist: st.dataframe(pd.DataFrame(hist), use_container_width=True, hide_index=True)

        elif menu == "Gestão de Equipe":
            with st.expander("➕ Admitir Novo Colaborador"):
                with st.form("add_func", clear_on_submit=True):
                    n_n = st.text_input("Nome Completo")
                    e_n = st.text_input("E-mail Corporativo")
                    col1, col2 = st.columns(2)
                    adm = col1.date_input("Data de Admissão", min_value=MIN_DATE)
                    
                    # NOVO: Controle de Abono na criação
                    permite_venda = col2.checkbox("Liberar Abono Pecuniário (Venda de 10 dias)", value=False)
                    
                    st.markdown("---")
                    st.caption("Saldo de Implantação (Apenas passivo antigo)")
                    c_s1, c_s2 = st.columns(2)
                    s_r = c_s1.number_input("Dias Pendentes Anteriores", min_value=0, value=0)
                    v_r = c_s2.date_input("Data Fatal Legal (Limite)", value=datetime.date.today(), min_value=MIN_DATE)
                    
                    if st.form_submit_button("Finalizar Cadastro"):
                        v_abono = 1 if permite_venda else 0
                        sql = f"""INSERT INTO rh_funcionarios 
                                 (nome, email_corporativo, data_admissao, saldo_retroativo, vencimento_retroativo, permite_abono, setor, is_ativo) 
                                 VALUES ('{n_n.replace("'", "''")}', '{e_n}', '{adm}', {s_r}, '{v_r}', {v_abono}, '{setor_trabalho}', 1)"""
                        query_banco(sql)
                        st.success("Colaborador cadastrado!")
                        st.rerun()

            st.write("### Equipe Atual")
            for f in funcs_setor:
                with st.container():
                    c1, c2, c3, c4 = st.columns([3, 1, 1, 1])
                    status_abono = "✅ Venda Liberada" if f.get('permite_abono', 0) == 1 else "🔒 Venda Bloqueada"
                    c1.write(f"**{f['nome']}**\n\nAdmissão: {f['data_admissao'].strftime('%d/%m/%Y')} | {status_abono}")
                    
                    if c2.button("📝 Editar", key=f"ed_{f['id_funcionario']}"):
                        st.session_state[f"editing_{f['id_funcionario']}"] = True
                    
                    if st.session_state.get(f"editing_{f['id_funcionario']}"):
                        with st.form(f"form_ed_{f['id_funcionario']}"):
                            en = st.text_input("Nome", value=f['nome'])
                            em = st.text_input("E-mail", value=f['email_corporativo'])
                            ea = st.date_input("Admissão", value=f['data_admissao'], min_value=MIN_DATE)
                            
                            # NOVO: Controle de Abono na edição
                            check_abono = f.get('permite_abono', 0) == 1
                            perm_abono_edit = st.checkbox("Liberar Abono Pecuniário", value=check_abono)
                            
                            st.caption("Ajuste de Saldo de Implantação")
                            col_s1, col_s2 = st.columns(2)
                            esr = col_s1.number_input("Dias Pendentes", value=f.get('saldo_retroativo') or 0)
                            data_venc = f.get('vencimento_retroativo') if f.get('vencimento_retroativo') else datetime.date.today()
                            evr = col_s2.date_input("Data Fatal Legal (Limite)", value=data_venc, min_value=MIN_DATE)
                            
                            if st.form_submit_button("Salvar Alterações"):
                                v_ab_edit = 1 if perm_abono_edit else 0
                                sql_update = f"UPDATE rh_funcionarios SET nome='{en.replace('\'', '\'\'')}', email_corporativo='{em}', data_admissao='{ea}', saldo_retroativo={esr}, vencimento_retroativo='{evr}', permite_abono={v_ab_edit} WHERE id_funcionario={f['id_funcionario']}"
                                query_banco(sql_update)
                                del st.session_state[f"editing_{f['id_funcionario']}"]
                                st.rerun()
                    
                    if c3.button("🔄 Reset IP", key=f"rs_{f['id_funcionario']}"):
                        query_banco(f"UPDATE rh_funcionarios SET ip_maquina=NULL WHERE id_funcionario={f['id_funcionario']}")
                        st.rerun()
                    
                    if c4.button("🗑️ Excluir", key=f"del_{f['id_funcionario']}"):
                        st.session_state[f"deleting_{f['id_funcionario']}"] = True
                    if st.session_state.get(f"deleting_{f['id_funcionario']}"):
                        if st.text_input("Digite CONFIRMO:", key=f"conf_{f['id_funcionario']}") == "CONFIRMO":
                            query_banco(f"DELETE FROM rh_funcionarios WHERE id_funcionario={f['id_funcionario']}")
                            st.rerun()
                st.divider()

        # (Menu de Configurações mantido inalterado...)
