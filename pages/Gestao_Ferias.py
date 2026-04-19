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
    st.error("Erro: Arquivo 'database.py' não encontrado na raiz.")
    st.stop()

def get_remote_ip():
    try:
        return st.context.headers.get("X-Forwarded-For", "127.0.0.1").split(',')[0]
    except:
        return "127.0.0.1"

st.set_page_config(page_title="Gestão de Férias", layout="wide")
current_ip = get_remote_ip()

st.title("📅 Sistema Estratégico de Férias")

# 2. CARREGAMENTO DE DADOS
try:
    funcionarios_db = query_banco("SELECT * FROM rh_funcionarios WHERE is_ativo = True ORDER BY nome ASC")
except Exception as e:
    st.error(f"Erro ao carregar banco: {e}")
    st.stop()

tab_func, tab_lider = st.tabs(["👤 Espaço do Funcionário", "🔒 Área Restrita (Líder)"])

# --- 3. ESPAÇO DO FUNCIONÁRIO (COM AUTO-VÍNCULO DE IP) ---
with tab_func:
    if not funcionarios_db:
        st.info("Nenhum funcionário cadastrado.")
    else:
        nomes = [f['nome'] for f in funcionarios_db]
        selecionado = st.selectbox("Selecione seu nome para acessar:", [""] + nomes, key="sel_func")

        if selecionado:
            user = next(item for item in funcionarios_db if item["nome"] == selecionado)
            
            if not user['ip_maquina'] or user['ip_maquina'] == "":
                st.warning("⚠️ ATENÇÃO: PRIMEIRO ACESSO DETECTADO")
                st.markdown(f"O sistema vinculará seu perfil permanentemente a esta máquina (IP: **{current_ip}**).")
                if st.button("Sim, confirmar identidade e vincular máquina"):
                    query_banco(f"UPDATE rh_funcionarios SET ip_maquina='{current_ip}' WHERE id_funcionario={user['id_funcionario']}")
                    st.rerun()
            
            elif user['ip_maquina'] != current_ip:
                st.error(f"🚫 ACESSO NEGADO: Máquina ({current_ip}) não autorizada.")
            
            else:
                st.success(f"Identidade validada (IP: {current_ip})")
                with st.form("form_solic_ferias", clear_on_submit=True):
                    col1, col2 = st.columns(2)
                    d_ini = col1.date_input("Data de Início")
                    d_fim = col2.date_input("Data de Retorno")
                    abono = st.checkbox("Desejo vender 10 dias") if bool(user['pode_vender_ferias']) else False
                    
                    if st.form_submit_button("Enviar Solicitação"):
                        total_dias = (d_fim - d_ini).days + 1
                        if total_dias < 5:
                            st.error("Mínimo de 5 dias.")
                        else:
                            query_banco(f"INSERT INTO rh_movimentacao_ferias (id_funcionario, data_inicio, data_fim, dias_corridos, abono_pecuniario, ip_registro, status) VALUES ({user['id_funcionario']}, '{d_ini}', '{d_fim}', {total_dias}, {abono}, '{current_ip}', 'Pendente')")
                            st.success("✅ Enviado!")

# --- 4. ÁREA RESTRITA (LÍDER) ---
with tab_lider:
    senha_lider = st.text_input("Senha de Gestão:", type="password")
    if senha_lider == "123":
        st.divider()
        menu = st.sidebar.radio("Navegação:", ["Aprovações Pendentes", "Dossiê por Setor", "Gerenciar Equipe"])

        # 4.1 APROVAÇÕES COM JUSTIFICATIVA OBRIGATÓRIA
        if menu == "Aprovações Pendentes":
            st.subheader("📩 Solicitações para Análise")
            pendentes = query_banco("SELECT f.nome, m.* FROM rh_movimentacao_ferias m JOIN rh_funcionarios f ON m.id_funcionario = f.id_funcionario WHERE m.status = 'Pendente'")
            
            if not pendentes:
                st.info("Nenhuma solicitação pendente.")
            else:
                for p in pendentes:
                    with st.expander(f"Pedido: {p['nome']} ({p['dias_corridos']} dias)"):
                        st.write(f"**Período:** {p['data_inicio'].strftime('%d/%m/%Y')} a {p['data_fim'].strftime('%d/%m/%Y')}")
                        
                        col_a, col_r = st.columns(2)
                        if col_a.button("✅ Aprovar", key=f"ap_{p['id_movimento']}"):
                            query_banco(f"UPDATE rh_movimentacao_ferias SET status='Aprovado' WHERE id_movimento={p['id_movimento']}")
                            st.rerun()
                        
                        # Interface de Recusa
                        st.write("---")
                        motivo = st.text_input("Justificativa para Recusa (Obrigatório):", key=f"mot_{p['id_movimento']}")
                        if col_r.button("❌ Recusar Pedido", key=f"re_{p['id_movimento']}"):
                            if motivo:
                                query_banco(f"UPDATE rh_movimentacao_ferias SET status='Recusado', motivo_recusa='{motivo}' WHERE id_movimento={p['id_movimento']}")
                                st.rerun()
                            else:
                                st.warning("Para recusar, você deve preencher a justificativa acima.")

        # 4.2 DOSSIÊ DIVIDIDO POR SETORES
        elif menu == "Dossiê por Setor":
            setor_sel = st.selectbox("Filtrar Dossiê por Setor:", ["Todos", "Contabilidade", "RH", "Fiscal"])
            
            # Filtra a lista de funcionários para o Dossiê
            funcs_filtrados = [f for f in funcionarios_db if f['setor'] == setor_sel] if setor_sel != "Todos" else funcionarios_db
            
            for f in funcs_filtrados:
                data_limite = f['data_admissao'] + datetime.timedelta(days=700) 
                meses_restantes = (data_limite - datetime.date.today()).days // 30
                
                with st.expander(f"[{f['setor']}] {f['nome']} - Admissão: {f['data_admissao'].strftime('%d/%m/%Y')}"):
                    c1, c2 = st.columns([1, 2])
                    if meses_restantes < 3: c1.error(f"🚨 Crítico: {meses_restantes} meses")
                    else: c1.success(f"✅ {meses_restantes} meses restantes")
                    
                    hist = query_banco(f"SELECT data_inicio, data_fim, status, motivo_recusa FROM rh_movimentacao_ferias WHERE id_funcionario={f['id_funcionario']} ORDER BY data_inicio DESC")
                    if hist: c2.dataframe(pd.DataFrame(hist))
                    else: c2.info("Sem histórico.")

        # 4.3 GERENCIAR EQUIPE (CADASTRO COM SETOR)
        elif menu == "Gerenciar Equipe":
            with st.expander("➕ Admitir Novo Funcionário"):
                with st.form("cad_novo"):
                    n_nome = st.text_input("Nome").replace("'", "''")
                    n_adm = st.date_input("Admissão")
                    n_setor = st.selectbox("Setor", ["Contabilidade", "RH", "Fiscal"])
                    if st.form_submit_button("Cadastrar"):
                        query_banco(f"INSERT INTO rh_funcionarios (nome, data_admissao, setor, is_ativo) VALUES ('{n_nome}', '{n_adm}', '{n_setor}', True)")
                        st.rerun()

            for f in funcionarios_db:
                with st.container():
                    col_n, col_v, col_ip, col_del = st.columns([2, 1, 1, 1])
                    col_n.write(f"**{f['nome']}** ({f['setor']})")
                    if col_v.toggle("Abono", value=bool(f['pode_vender_ferias']), key=f"v_{f['id_funcionario']}") != bool(f['pode_vender_ferias']):
                        query_banco(f"UPDATE rh_funcionarios SET pode_vender_ferias = NOT pode_vender_ferias WHERE id_funcionario={f['id_funcionario']}")
                        st.rerun()
                    if col_ip.button("Resetar IP", key=f"ip_{f['id_funcionario']}"):
                        query_banco(f"UPDATE rh_funcionarios SET ip_maquina=NULL WHERE id_funcionario={f['id_funcionario']}")
                        st.rerun()
                    if col_del.button("🗑️", key=f"del_{f['id_funcionario']}"):
                        if st.text_input("Confirme com CONFIRMO", key=f"c_{f['id_funcionario']}") == "CONFIRMO":
                            query_banco(f"DELETE FROM rh_funcionarios WHERE id_funcionario={f['id_funcionario']}")
                            st.rerun()
                st.divider()

    elif senha_lider != "":
        st.error("Senha incorreta.")
