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
                    aprovados = query_banco(f"SELECT * FROM rh_movimentacao_ferias WHERE id_funcionario={user['id_funcionario']} AND status='Aprovado'")
                    if not aprovados:
                        st.info("Você não possui férias aprovadas para reagendar.")
                    else:
                        for f_ap in aprovados:
                            inicio_f = f_ap['data_inicio'].strftime('%d/%m/%Y')
                            fim_f = f_ap['data_fim'].strftime('%d/%m/%Y')
                            with st.expander(f"🔄 Solicitar alteração para férias de {inicio_f} a {fim_f}"):
                                motivo_re = st.text_area("Justifique o motivo do reagendamento:", key=f"mot_re_{f_ap['id_movimento']}")
                                if st.button("Enviar Pedido de Alteração", key=f"btn_re_{f_ap['id_movimento']}"):
                                    if motivo_re:
                                        query_banco(f"UPDATE rh_movimentacao_ferias SET status='Reagendamento Solicitado', motivo_recusa='{motivo_re}' WHERE id_movimento={f_ap['id_movimento']}")
                                        st.success("Pedido enviado.")
                                        st.rerun()
                                    else: st.warning("Descreva o motivo.")

# --- 4. ÁREA RESTRITA (LÍDER) ---
with tab_lider:
    c_l1, c_l2 = st.columns(2)
    senha = c_l1.text_input("Senha:", type="password")
    setor_trabalho = c_l2.selectbox("Setor:", setores_lista)
    
    if senha == "123":
        funcs_setor = [f for f in funcionarios_db if f['setor'] == setor_trabalho]
        st.sidebar.markdown(f"### 🏢 Setor: {setor_trabalho}")
        menu = st.sidebar.radio("Navegação:", ["Aprovações", "Dossiê Estratégico", "Gestão de Equipe", "⚙️ Configurações"])

        # 4.1 APROVAÇÕES E MENSAGENS PADRÃO
        if menu == "Aprovações":
            pendentes = query_banco(f"""SELECT f.nome, f.email_corporativo, m.* FROM rh_movimentacao_ferias m 
                                       JOIN rh_funcionarios f ON m.id_funcionario = f.id_funcionario 
                                       WHERE m.status IN ('Pendente', 'Reagendamento Solicitado') AND f.setor = '{setor_trabalho}'""")
            if not pendentes: st.info("Nada pendente.")
            
            for p in pendentes:
                with st.expander(f"{p['nome']} - {p['dias_corridos']} dias ({p['data_inicio'].strftime('%d/%m/%Y')})"):
                    if p['status'] == 'Reagendamento Solicitado':
                        st.warning(f"Motivo Reagendamento: {p['motivo_recusa']}")
                    
                    ca, cr = st.columns(2)
                    if ca.button("✅ Aprovar", key=f"ap_{p['id_movimento']}"):
                        query_banco(f"UPDATE rh_movimentacao_ferias SET status='Aprovado' WHERE id_movimento={p['id_movimento']}")
                        
                        # --- INÍCIO: DISPARO DE MENSAGENS PROFISSIONAIS PADRÃO ---
                        config = query_banco(f"SELECT * FROM rh_configuracoes_setores WHERE setor = '{setor_trabalho}'")
                        if config:
                            c = config[0]
                            periodo = f"{p['data_inicio'].strftime('%d/%m/%Y')} a {p['data_fim'].strftime('%d/%m/%Y')}"
                            
                            # 1. E-mail Funcionário
                            txt_func = f"Olá {p['nome']},\n\nSua solicitação de férias foi APROVADA!\nPeríodo: {periodo}.\n\nPor favor, organize suas pendências antes do período de descanso."
                            enviar_email(p['email_corporativo'], "✅ Confirmação de Férias", txt_func)
                            
                            # 2. E-mail RH
                            txt_rh = f"Notificação de Sistema:\n\nO colaborador {p['nome']} (Setor: {setor_trabalho}) teve suas férias aprovadas.\nPeríodo programado: {periodo}.\n\nFavor programar as rotinas de D.P."
                            enviar_email(c['email_rh'], f"📌 Aprovação Escala: {p['nome']}", txt_rh)
                            
                            # 3. E-mail Setor Vinculado (O Flag)
                            if c['setor_vinculado'] and c['setor_vinculado'] != "Nenhum":
                                vinc = query_banco(f"SELECT email_lider FROM rh_configuracoes_setores WHERE setor = '{c['setor_vinculado']}'")
                                if vinc:
                                    txt_vinc = f"Informativo de Escala Vinculada:\n\nO colaborador {p['nome']} do setor {setor_trabalho} estará ausente no período de {periodo}.\n\nRecomendamos alinhar demandas pendentes."
                                    enviar_email(vinc[0]['email_lider'], "📢 Aviso de Férias (Setor Vinculado)", txt_vinc)
                        # --- FIM: DISPARO ---
                        
                        st.success("Aprovado e Notificado!")
                        st.rerun()

                    # Recusa
                    mot_rec = st.text_input("Justificativa da Recusa:", key=f"j_{p['id_movimento']}")
                    if cr.button("❌ Recusar", key=f"r_{p['id_movimento']}"):
                        if mot_rec:
                            query_banco(f"UPDATE rh_movimentacao_ferias SET status='Recusado', motivo_recusa='{mot_rec}' WHERE id_movimento={p['id_movimento']}")
                            st.rerun()
                        else: st.warning("Informe o motivo.")

        # 4.2 DOSSIÊ ESTRATÉGICO
        elif menu == "Dossiê Estratégico":
            for f in funcs_setor:
                limite = f['data_admissao'] + datetime.timedelta(days=700)
                dias_rest = (limite - datetime.date.today()).days
                with st.expander(f"{f['nome']} (Prazo: {limite.strftime('%d/%m/%Y')})"):
                    st.write(f"Tempo restante: {dias_rest // 30} meses")
                    hist = query_banco(f"SELECT data_inicio, data_fim, status FROM rh_movimentacao_ferias WHERE id_funcionario={f['id_funcionario']} ORDER BY data_inicio DESC")
                    if hist: st.table(pd.DataFrame(hist))

        # 4.3 GESTÃO DE EQUIPE
        elif menu == "Gestão de Equipe":
            with st.expander("➕ Admitir Novo Colaborador"):
                with st.form("add_func", clear_on_submit=True):
                    n_n = st.text_input("Nome")
                    e_n = st.text_input("E-mail")
                    a_n = st.date_input("Admissão")
                    if st.form_submit_button("Finalizar"):
                        sql = f"INSERT INTO rh_funcionarios (nome, email_corporativo, data_admissao, setor, is_ativo) VALUES ('{n_n.replace('\'', '\'\'')}', '{e_n}', '{a_n.strftime('%Y-%m-%d')}', '{setor_trabalho}', 1)"
                        query_banco(sql)
                        st.success("Cadastrado!")
                        st.rerun()
            
            st.write("### Equipe Atual")
            for f in funcs_setor:
                with st.container():
                    c1, c2, c3, c4 = st.columns([3, 1, 1, 1])
                    c1.write(f"**{f['nome']}**\n\n*{f['email_corporativo']}*")
                    
                    if c2.button("📝 Editar", key=f"ed_{f['id_funcionario']}"):
                        st.session_state[f"editing_{f['id_funcionario']}"] = True
                    
                    if st.session_state.get(f"editing_{f['id_funcionario']}"):
                        with st.form(f"form_ed_{f['id_funcionario']}"):
                            en = st.text_input("Nome", value=f['nome'])
                            em = st.text_input("E-mail", value=f['email_corporativo'])
                            ea = st.date_input("Admissão", value=f['data_admissao'])
                            es = st.selectbox("Setor", setores_lista, index=setores_lista.index(f['setor']))
                            if st.form_submit_button("Salvar"):
                                query_banco(f"UPDATE rh_funcionarios SET nome='{en.replace('\'', '\'\'')}', email_corporativo='{em}', data_admissao='{ea.strftime('%Y-%m-%d')}', setor='{es}' WHERE id_funcionario={f['id_funcionario']}")
                                del st.session_state[f"editing_{f['id_funcionario']}"]
                                st.rerun()

                    if c3.button("🔄 Reset IP", key=f"rs_{f['id_funcionario']}"):
                        query_banco(f"UPDATE rh_funcionarios SET ip_maquina=NULL WHERE id_funcionario={f['id_funcionario']}")
                        st.toast("IP resetado!")
                        st.rerun()

                    if c4.button("🗑️ Excluir", key=f"del_{f['id_funcionario']}"):
                        st.session_state[f"deleting_{f['id_funcionario']}"] = True
                    if st.session_state.get(f"deleting_{f['id_funcionario']}"):
                        if st.text_input("Digite CONFIRMO:", key=f"conf_{f['id_funcionario']}") == "CONFIRMO":
                            query_banco(f"DELETE FROM rh_funcionarios WHERE id_funcionario={f['id_funcionario']}")
                            st.rerun()
                st.divider()

        # 4.4 CONFIGURAÇÕES DE SETOR (Novo!)
        elif menu == "⚙️ Configurações":
            st.subheader(f"Parâmetros do Setor: {setor_trabalho}")
            
            # Busca as configurações atuais para preencher o formulário
            cfg_atual = query_banco(f"SELECT * FROM rh_configuracoes_setores WHERE setor = '{setor_trabalho}'")
            dados = cfg_atual[0] if cfg_atual else {"email_lider": "", "email_rh": "", "email_diretoria": "", "setor_vinculado": "Nenhum"}
            
            with st.form("form_config"):
                e_lider = st.text_input("📧 E-mail do Líder deste setor:", value=dados.get("email_lider"))
                e_rh = st.text_input("📧 E-mail do RH/D.P para cópias:", value=dados.get("email_rh"))
                e_dir = st.text_input("📧 E-mail da Diretoria (Opcional):", value=dados.get("email_diretoria"))
                
                vinc_idx = setores_vinculo.index(dados.get("setor_vinculado")) if dados.get("setor_vinculado") in setores_vinculo else 0
                s_vinc = st.selectbox("🔗 Setor Vinculado (Recebe avisos de escala):", setores_vinculo, index=vinc_idx)
                
                if st.form_submit_button("Salvar Configurações"):
                    if cfg_atual:
                        sql_cfg = f"UPDATE rh_configuracoes_setores SET email_lider='{e_lider}', email_rh='{e_rh}', email_diretoria='{e_dir}', setor_vinculado='{s_vinc}' WHERE setor='{setor_trabalho}'"
                    else:
                        sql_cfg = f"INSERT INTO rh_configuracoes_setores (setor, email_lider, email_rh, email_diretoria, setor_vinculado) VALUES ('{setor_trabalho}', '{e_lider}', '{e_rh}', '{e_dir}', '{s_vinc}')"
                    
                    query_banco(sql_cfg)
                    st.success("Configurações atualizadas com sucesso!")
                    st.rerun()
