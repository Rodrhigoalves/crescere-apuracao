import streamlit as st
import sys
import os
import datetime
import pandas as pd

# 1. CONEXÃO E INFRAESTRUTURA
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))
try:
    from database import query_banco
except ImportError:
    st.error("Erro: Arquivo 'database.py' não encontrado.")
    st.stop()

def get_remote_ip():
    try:
        return st.context.headers.get("X-Forwarded-For", "127.0.0.1").split(',')[0]
    except:
        return "127.0.0.1"

st.set_page_config(page_title="Gestão de Férias", layout="wide")
current_ip = get_remote_ip()
setores_lista = ["CONTÁBIL", "D.P", "FISCAL"]

st.title("📅 Sistema Estratégico de Férias")

# 2. CARREGAMENTO DE DADOS
try:
    funcionarios_db = query_banco("SELECT * FROM rh_funcionarios WHERE is_ativo = True ORDER BY nome ASC")
except Exception as e:
    st.error(f"Erro ao carregar banco: {e}")
    st.stop()

tab_func, tab_lider = st.tabs(["👤 Espaço do Funcionário", "🔒 Área Restrita (Líder)"])

# --- 3. ESPAÇO DO FUNCIONÁRIO (IP + REAGENDAMENTO) ---
with tab_func:
    if not funcionarios_db:
        st.info("Nenhum funcionário cadastrado.")
    else:
        nomes = [f['nome'] for f in funcionarios_db]
        selecionado = st.selectbox("Selecione seu nome:", [""] + nomes, key="sel_func")

        if selecionado:
            user = next(item for item in funcionarios_db if item["nome"] == selecionado)
            
            # Validação de IP (Auto-vínculo)
            if not user['ip_maquina']:
                st.warning("⚠️ PRIMEIRO ACESSO: Vincule sua máquina para prosseguir.")
                st.info(f"Sua máquina atual possui o IP: {current_ip}")
                if st.button("Confirmar Identidade e Vincular Máquina"):
                    query_banco(f"UPDATE rh_funcionarios SET ip_maquina='{current_ip}' WHERE id_funcionario={user['id_funcionario']}")
                    st.rerun()
            elif user['ip_maquina'] != current_ip:
                st.error("🚫 Acesso Negado: Esta máquina não é sua estação autorizada.")
                st.caption(f"IP desta máquina: {current_ip} | IP Autorizado: {user['ip_maquina']}")
            else:
                st.success(f"Conectado: {selecionado} (Setor: {user['setor']})")
                sub_tab1, sub_tab2 = st.tabs(["📝 Nova Solicitação", "🔄 Reagendar Férias"])
                
                with sub_tab1:
                    with st.form("form_nova"):
                        c1, c2 = st.columns(2)
                        d_ini = c1.date_input("Início das Férias")
                        d_fim = c2.date_input("Último dia de Descanso")
                        abono = st.checkbox("Vender 10 dias (Abono)") if user['pode_vender_ferias'] else False
                        if st.form_submit_button("Enviar Solicitação"):
                            dias = (d_fim - d_ini).days + 1
                            if dias < 5: st.error("O período mínimo deve ser de 5 dias.")
                            else:
                                query_banco(f"INSERT INTO rh_movimentacao_ferias (id_funcionario, data_inicio, data_fim, dias_corridos, abono_pecuniario, ip_registro, status) VALUES ({user['id_funcionario']}, '{d_ini}', '{d_fim}', {dias}, {abono}, '{current_ip}', 'Pendente')")
                                st.success("Solicitação enviada com sucesso!")

                with sub_tab2:
                    aprovados = query_banco(f"SELECT * FROM rh_movimentacao_ferias WHERE id_funcionario={user['id_funcionario']} AND status='Aprovado'")
                    if not aprovados:
                        st.info("Você não possui férias aprovadas para reagendar.")
                    else:
                        for f_ap in aprovados:
                            with st.expander(f"Férias aprovadas para {f_ap['data_inicio'].strftime('%d/%m/%Y')}"):
                                motivo_re = st.text_area("Justifique o motivo do reagendamento:", key=f"mot_re_{f_ap['id_movimento']}")
                                if st.button("Solicitar Alteração", key=f"btn_re_{f_ap['id_movimento']}"):
                                    if motivo_re:
                                        query_banco(f"UPDATE rh_movimentacao_ferias SET status='Reagendamento Solicitado', motivo_recusa='{motivo_re}' WHERE id_movimento={f_ap['id_movimento']}")
                                        st.success("Pedido de alteração enviado ao líder.")
                                        st.rerun()
                                    else: st.warning("Por favor, descreva o motivo.")

# --- 4. ÁREA RESTRITA (LÍDER COM FILTRO DE SETOR NA ENTRADA) ---
with tab_lider:
    # FILTRO MESTRE: Senha + Setor
    col_s1, col_s2 = st.columns(2)
    senha_lider = col_s1.text_input("Senha de Gestão:", type="password")
    setor_trabalho = col_s2.selectbox("Selecione o setor para gerenciar:", setores_lista)
    
    if senha_lider == "123": # <--- Altere sua senha aqui
        # Filtra automaticamente os dados com base na escolha inicial
        funcs_setor = [f for f in funcionarios_db if f['setor'] == setor_trabalho]
        
        st.sidebar.markdown(f"### 🏢 Setor: {setor_trabalho}")
        menu = st.sidebar.radio("Navegação:", ["Aprovações", "Dossiê Estratégico", "Gestão de Equipe"])

        # 4.1 APROVAÇÕES (FILTRADAS PELO SETOR)
        if menu == "Aprovações":
            st.subheader(f"📩 Pedidos Pendentes - {setor_trabalho}")
            pendentes = query_banco(f"""
                SELECT f.nome, m.* FROM rh_movimentacao_ferias m 
                JOIN rh_funcionarios f ON m.id_funcionario = f.id_funcionario 
                WHERE m.status IN ('Pendente', 'Reagendamento Solicitado') AND f.setor = '{setor_trabalho}'
            """)
            if not pendentes: st.info(f"Nenhuma solicitação pendente para o setor {setor_trabalho}.")
            else:
                for p in pendentes:
                    tipo = "SOLICITAÇÃO" if p['status'] == 'Pendente' else "REAGENDAMENTO"
                    with st.expander(f"[{tipo}] {p['nome']} - {p['dias_corridos']} dias"):
                        if tipo == "REAGENDAMENTO": st.error(f"Motivo do Pedido: {p['motivo_recusa']}")
                        
                        ca, cr = st.columns(2)
                        if ca.button("✅ Aprovar", key=f"a_{p['id_movimento']}"):
                            query_banco(f"UPDATE rh_movimentacao_ferias SET status='Aprovado' WHERE id_movimento={p['id_movimento']}")
                            st.rerun()
                        
                        st.write("---")
                        just_neg = st.text_input("Justificativa caso vá Recusar:", key=f"neg_{p['id_movimento']}")
                        if cr.button("❌ Recusar", key=f"r_{p['id_movimento']}"):
                            if just_neg:
                                query_banco(f"UPDATE rh_movimentacao_ferias SET status='Recusado', motivo_recusa='{just_neg}' WHERE id_movimento={p['id_movimento']}")
                                st.rerun()
                            else: st.warning("Para recusar, escreva a justificativa acima.")

        # 4.2 DOSSIÊ E CONTADOR (FILTRADO)
        elif menu == "Dossiê Estratégico":
            st.subheader(f"📊 Inteligência e Prazos - {setor_trabalho}")
            if not funcs_setor: st.info("Nenhum funcionário neste setor.")
            for f in funcs_setor:
                limite = f['data_admissao'] + datetime.timedelta(days=700) # Projeção 23 meses
                meses = (limite - datetime.date.today()).days // 30
                with st.expander(f"{f['nome']} (Admissão: {f['data_admissao'].strftime('%d/%m/%Y')})"):
                    c1, c2 = st.columns([1, 2])
                    if meses < 3: c1.error(f"🚨 CRÍTICO: {meses} meses p/ multa!")
                    elif meses < 6: c1.warning(f"⚠️ ATENÇÃO: {meses} meses restantes.")
                    else: c1.success(f"✅ Seguro: {meses} meses.")
                    
                    hist = query_banco(f"SELECT data_inicio, data_fim, status, motivo_recusa FROM rh_movimentacao_ferias WHERE id_funcionario={f['id_funcionario']} ORDER BY data_inicio DESC")
                    if hist: st.table(pd.DataFrame(hist))

        # 4.3 GESTÃO DE EQUIPE (FILTRADO + EDIÇÃO)
        elif menu == "Gestão de Equipe":
            with st.expander("➕ Admitir Novo Colaborador"):
                with st.form("add_func"):
                    nn = st.text_input("Nome").replace("'", "''")
                    na = st.date_input("Admissão")
                    ns = st.selectbox("Setor", setores_lista, index=setores_lista.index(setor_trabalho))
                    if st.form_submit_button("Cadastrar"):
                        query_banco(f"INSERT INTO rh_funcionarios (nome, data_admissao, setor, is_ativo) VALUES ('{nn}', '{na}', '{ns}', True)")
                        st.rerun()

            st.write(f"### Equipe: {setor_trabalho}")
            for f in funcs_setor:
                with st.container():
                    c1, c2, c3, c4 = st.columns([2, 1, 1, 1])
                    c1.write(f"**{f['nome']}**")
                    
                    # Edição
                    if c2.button("📝 Editar", key=f"ed_{f['id_funcionario']}"):
                        st.session_state[f"editing_{f['id_funcionario']}"] = True
                    
                    if st.session_state.get(f"editing_{f['id_funcionario']}"):
                        with st.form(f"f_ed_{f['id_funcionario']}"):
                            en = st.text_input("Nome", value=f['nome'])
                            ea = st.date_input("Admissão", value=f['data_admissao'])
                            es = st.selectbox("Setor", setores_lista, index=setores_lista.index(f['setor']))
                            if st.form_submit_button("Salvar"):
                                query_banco(f"UPDATE rh_funcionarios SET nome='{en}', data_admissao='{ea}', setor='{es}' WHERE id_funcionario={f['id_funcionario']}")
                                del st.session_state[f"editing_{f['id_funcionario']}"]
                                st.rerun()
                    
                    # Reset IP
                    if c3.button("Reset IP", key=f"ip_{f['id_funcionario']}"):
                        query_banco(f"UPDATE rh_funcionarios SET ip_maquina=NULL WHERE id_funcionario={f['id_funcionario']}")
                        st.success("Resetado!")
                    
                    # Excluir
                    if c4.button("🗑️", key=f"d_{f['id_funcionario']}"):
                        if st.text_input("Confirme com CONFIRMO", key=f"conf_{f['id_funcionario']}") == "CONFIRMO":
                            query_banco(f"DELETE FROM rh_funcionarios WHERE id_funcionario={f['id_funcionario']}")
                            st.rerun()
                st.divider()

    elif senha_lider != "":
        st.error("Senha incorreta.")
