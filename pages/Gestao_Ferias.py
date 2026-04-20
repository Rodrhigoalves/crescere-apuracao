import streamlit as st
import sys
import os
import datetime
import pandas as pd

# 1. AJUSTE DE CAMINHO PARA IMPORTAR DA RAIZ
# Adiciona o diretório pai ao sys.path para localizar database.py e mailer.py
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

try:
    from database import query_banco
    from mailer import enviar_email
except Exception as e:
    st.error(f"Erro de infraestrutura: {e}")
    st.stop()

# Configuração da Página
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
    funcionarios_db = query_banco("SELECT * FROM rh_funcionarios WHERE is_ativo = True ORDER BY nome ASC")
except Exception as e:
    st.error(f"Erro ao conectar com o banco de dados: {e}")
    st.stop()

tab_func, tab_lider = st.tabs(["👤 Espaço do Funcionário", "🔒 Área Restrita (Líder)"])

# --- 3. ESPAÇO DO FUNCIONÁRIO ---
with tab_func:
    if not funcionarios_db:
        st.info("Nenhum funcionário cadastrado no sistema.")
    else:
        nomes = [f['nome'] for f in funcionarios_db]
        selecionado = st.selectbox("Selecione seu nome:", [""] + nomes, key="sel_func")

        if selecionado:
            user = next(item for item in funcionarios_db if item["nome"] == selecionado)
            
            # Validação de IP (Segurança)
            if not user['ip_maquina']:
                st.warning("⚠️ PRIMEIRO ACESSO: Esta máquina ainda não está vinculada ao seu usuário.")
                if st.button("Confirmar Identidade e Vincular Máquina"):
                    query_banco(f"UPDATE rh_funcionarios SET ip_maquina='{current_ip}' WHERE id_funcionario={user['id_funcionario']}")
                    st.success("Máquina vinculada! Recarregando...")
                    st.rerun()
            elif user['ip_maquina'] != current_ip:
                st.error(f"🚫 ACESSO NEGADO: Esta estação de trabalho não é a autorizada para {selecionado}.")
                st.caption(f"IP Atual: {current_ip} | IP Autorizado: {user['ip_maquina']}")
            else:
                st.success(f"Bem-vindo, {selecionado}! (Setor: {user['setor']})")
                sub1, sub2 = st.tabs(["📝 Nova Solicitação", "🔄 Reagendar/Status"])
                
                with sub1:
                    with st.form("form_nova"):
                        c1, c2 = st.columns(2)
                        d_ini = c1.date_input("Início das Férias")
                        d_fim = c2.date_input("Fim do Descanso")
                        abono = st.checkbox("Abono Pecuniário (Vender 10 dias)")
                        if st.form_submit_button("Enviar para Aprovação"):
                            dias = (d_fim - d_ini).days + 1
                            if dias < 5:
                                st.error("O período mínimo permitido é de 5 dias.")
                            else:
                                query_banco(f"INSERT INTO rh_movimentacao_ferias (id_funcionario, data_inicio, data_fim, dias_corridos, abono_pecuniario, status, ip_registro) VALUES ({user['id_funcionario']}, '{d_ini}', '{d_fim}', {dias}, {abono}, 'Pendente', '{current_ip}')")
                                st.success("Solicitação enviada com sucesso! Seu líder receberá um e-mail.")

                with sub2:
                    historico_pessoal = query_banco(f"SELECT data_inicio, data_fim, status, motivo_recusa FROM rh_movimentacao_ferias WHERE id_funcionario={user['id_funcionario']} ORDER BY data_inicio DESC")
                    if historico_pessoal:
                        st.table(pd.DataFrame(historico_pessoal))

# --- 4. ÁREA RESTRITA (LÍDER) ---
with tab_lider:
    col_l1, col_l2 = st.columns(2)
    senha_lider = col_l1.text_input("Senha de Acesso:", type="password")
    setor_trabalho = col_l2.selectbox("Setor sob sua Gestão:", setores_lista)
    
    if senha_lider == "123": # Altere conforme sua necessidade
        funcs_setor = [f for f in funcionarios_db if f['setor'] == setor_trabalho]
        
        st.sidebar.markdown(f"### 🏢 Gestão: {setor_trabalho}")
        menu = st.sidebar.radio("Navegação:", ["Aprovações", "Dossiê Estratégico", "Gestão de Equipe"])

        # 4.1 APROVAÇÕES E NOTIFICAÇÕES
        if menu == "Aprovações":
            st.subheader(f"📩 Pedidos Pendentes - {setor_trabalho}")
            pendentes = query_banco(f"""
                SELECT f.nome, f.email_corporativo, m.* FROM rh_movimentacao_ferias m 
                JOIN rh_funcionarios f ON m.id_funcionario = f.id_funcionario 
                WHERE m.status IN ('Pendente', 'Reagendamento Solicitado') AND f.setor = '{setor_trabalho}'
            """)
            
            if not pendentes:
                st.info("Não há solicitações aguardando resposta.")
            else:
                for p in pendentes:
                    with st.expander(f"{p['nome']} - {p['dias_corridos']} dias ({p['data_inicio'].strftime('%d/%m/%Y')})"):
                        ca, cr = st.columns(2)
                        
                        if ca.button("✅ Aprovar", key=f"aprov_{p['id_movimento']}"):
                            query_banco(f"UPDATE rh_movimentacao_ferias SET status='Aprovado' WHERE id_movimento={p['id_movimento']}")
                            
                            # Lógica de Notificação Automática
                            config = query_banco(f"SELECT * FROM rh_configuracoes_setores WHERE setor = '{setor_trabalho}'")
                            if config:
                                c = config[0]
                                periodo = f"{p['data_inicio'].strftime('%d/%m/%Y')} a {p['data_fim'].strftime('%d/%m/%Y')}"
                                
                                # 1. Para o Funcionário
                                enviar_email(p['email_corporativo'], "✅ Férias Confirmadas", f"Olá {p['nome']}, suas férias para o período {periodo} foram aprovadas pelo líder.")
                                # 2. Para o RH
                                enviar_email(c['email_rh'], "📌 Novo Agendamento", f"O funcionário {p['nome']} ({setor_trabalho}) sairá de férias de {periodo}.")
                                # 3. Para Setor Vinculado
                                if c['setor_vinculado']:
                                    vinc = query_banco(f"SELECT email_lider FROM rh_configuracoes_setores WHERE setor = '{c['setor_vinculado']}'")
                                    if vinc:
                                        enviar_email(vinc[0]['email_lider'], "📢 Aviso de Escala", f"Atenção: {p['nome']} ({setor_trabalho}) estará ausente de {periodo}.")
                            
                            st.success(f"Férias de {p['nome']} aprovadas!")
                            st.rerun()

                        motivo = st.text_input("Motivo da recusa (obrigatório):", key=f"mot_{p['id_movimento']}")
                        if cr.button("❌ Recusar", key=f"rec_{p['id_movimento']}"):
                            if motivo:
                                query_banco(f"UPDATE rh_movimentacao_ferias SET status='Recusado', motivo_recusa='{motivo}' WHERE id_movimento={p['id_movimento']}")
                                st.rerun()
                            else:
                                st.error("Informe o motivo para recusar.")

        # 4.2 DOSSIÊ E CONTADOR 23 MESES
        elif menu == "Dossiê Estratégico":
            st.subheader(f"📊 Inteligência de Prazos - {setor_trabalho}")
            for f in funcs_setor:
                limite = f['data_admissao'] + datetime.timedelta(days=700)
                dias_restantes = (limite - datetime.date.today()).days
                meses = dias_restantes // 30
                
                with st.expander(f"{f['nome']} (Admissão: {f['data_admissao'].strftime('%d/%m/%Y')})"):
                    c1, c2 = st.columns([1, 3])
                    if dias_restantes < 60: c1.error(f"🚨 {meses} meses p/ limite!")
                    elif dias_restantes < 180: c1.warning(f"⚠️ {meses} meses p/ limite.")
                    else: c1.success(f"✅ {meses} meses restantes.")
                    
                    hist = query_banco(f"SELECT data_inicio, data_fim, status FROM rh_movimentacao_ferias WHERE id_funcionario={f['id_funcionario']} ORDER BY data_inicio DESC")
                    if hist: st.dataframe(pd.DataFrame(hist), use_container_width=True)

        # 4.3 GESTÃO DE EQUIPE
        elif menu == "Gestão de Equipe":
            with st.expander("➕ Admitir Novo Colaborador"):
                with st.form("add_func"):
                    nome_novo = st.text_input("Nome Completo").replace("'", "''")
                    email_novo = st.text_input("E-mail Corporativo")
                    adm_nova = st.date_input("Data de Admissão")
                    if st.form_submit_button("Finalizar Cadastro"):
                        query_banco(f"INSERT INTO rh_funcionarios (nome, email_corporativo, data_admissao, setor, is_ativo) VALUES ('{nome_novo}', '{email_novo}', '{adm_nova}', '{setor_trabalho}', True)")
                        st.success("Cadastrado!")
                        st.rerun()

            st.write("### Integrantes do Setor")
            for f in funcs_setor:
                c1, c2, c3 = st.columns([3, 1, 1])
                c1.write(f"**{f['nome']}** ({f['email_corporativo']})")
                if c2.button("Reset IP", key=f"res_{f['id_funcionario']}"):
                    query_banco(f"UPDATE rh_funcionarios SET ip_maquina=NULL WHERE id_funcionario={f['id_funcionario']}")
                    st.rerun()
                if c3.button("🗑️", key=f"del_{f['id_funcionario']}"):
                    query_banco(f"DELETE FROM rh_funcionarios WHERE id_funcionario={f['id_funcionario']}")
                    st.rerun()
                st.divider()

    elif senha_lider != "":
        st.error("Acesso negado.")
