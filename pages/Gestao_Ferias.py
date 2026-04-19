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
        # Captura o IP (funciona no Streamlit Cloud e Local)
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
            
            # LÓGICA DE AUTO-VÍNCULO NO PRIMEIRO ACESSO
            if not user['ip_maquina'] or user['ip_maquina'] == "":
                st.warning("⚠️ ATENÇÃO: PRIMEIRO ACESSO DETECTADO")
                st.markdown(f"""
                Para sua segurança e privacidade, o sistema vinculará seu perfil permanentemente a esta máquina (IP: **{current_ip}**).
                
                **Antes de confirmar, certifique-se:** Você selecionou o nome **{selecionado}** corretamente?
                """)
                if st.button("Sim, confirmar minha identidade e vincular esta máquina"):
                    query_banco(f"UPDATE rh_funcionarios SET ip_maquina='{current_ip}' WHERE id_funcionario={user['id_funcionario']}")
                    st.success("Máquina vinculada com sucesso! Atualizando...")
                    st.rerun()
            
            # VALIDAÇÃO DE IP JÁ VINCULADO
            elif user['ip_maquina'] != current_ip:
                st.error(f"🚫 ACESSO NEGADO: Esta máquina ({current_ip}) não é sua estação autorizada.")
                st.info(f"O colaborador {selecionado} está vinculado a outro IP. Se você mudou de lugar, peça ao seu líder para resetar seu acesso.")
            
            # ACESSO LIBERADO
            else:
                st.success(f"Identidade validada via Protocolo de IP ({current_ip})")
                with st.form("form_solic_ferias", clear_on_submit=True):
                    col1, col2 = st.columns(2)
                    d_ini = col1.date_input("Data de Início")
                    d_fim = col2.date_input("Data de Retorno")
                    
                    # O Abono só aparece se o líder habilitou para este usuário no banco
                    if bool(user['pode_vender_ferias']):
                        abono = st.checkbox("Desejo vender 10 dias (Abono Pecuniário)")
                    else:
                        abono = False
                        st.info("Opção de venda de férias não disponível para seu perfil.")
                        
                    if st.form_submit_button("Enviar Solicitação Oficial"):
                        total_dias = (d_fim - d_ini).days + 1
                        if total_dias < 5:
                            st.error("O período mínimo de férias deve ser de 5 dias.")
                        else:
                            # Grava a solicitação com IP e Horário para o Dossiê
                            query_banco(f"""
                                INSERT INTO rh_movimentacao_ferias 
                                (id_funcionario, data_inicio, data_fim, dias_corridos, abono_pecuniario, ip_registro, status) 
                                VALUES ({user['id_funcionario']}, '{d_ini}', '{d_fim}', {total_dias}, {abono}, '{current_ip}', 'Pendente')
                            """)
                            st.success("✅ Solicitação enviada! Protocolo registrado com sucesso.")

# --- 4. ÁREA RESTRITA (LÍDER) ---
with tab_lider:
    senha_lider = st.text_input("Senha de Gestão:", type="password")
    if senha_lider == "123": # <--- Altere sua senha aqui
        st.divider()
        menu = st.sidebar.radio("Navegação:", ["Aprovações Pendentes", "Dossiê e Histórico", "Gerenciar Equipe"])

        # 4.1 PAINEL DE APROVAÇÕES
        if menu == "Aprovações Pendentes":
            st.subheader("📩 Solicitações para Análise")
            pendentes = query_banco("""
                SELECT f.nome, m.* FROM rh_movimentacao_ferias m 
                JOIN rh_funcionarios f ON m.id_funcionario = f.id_funcionario 
                WHERE m.status = 'Pendente'
            """)
            if not pendentes:
                st.info("Nenhuma solicitação pendente.")
            else:
                for p in pendentes:
                    with st.expander(f"Pedido: {p['nome']} (IP de Origem: {p['ip_registro']})"):
                        st.write(f"**Período:** {p['data_inicio'].strftime('%d/%m/%Y')} a {p['data_fim'].strftime('%d/%m/%Y')} ({p['dias_corridos']} dias)")
                        st.write(f"**Venda de 10 dias:** {'Sim' if p['abono_pecuniario'] else 'Não'}")
                        col_a, col_r = st.columns(2)
                        if col_a.button("✅ Aprovar", key=f"ap_{p['id_movimento']}"):
                            query_banco(f"UPDATE rh_movimentacao_ferias SET status='Aprovado' WHERE id_movimento={p['id_movimento']}")
                            st.rerun()
                        if col_r.button("❌ Recusar", key=f"re_{p['id_movimento']}"):
                            query_banco(f"UPDATE rh_movimentacao_ferias SET status='Recusado' WHERE id_movimento={p['id_movimento']}")
                            st.rerun()

        # 4.2 DOSSIÊ E INTELIGÊNCIA (CONTADOR E HISTÓRICO)
        elif menu == "Dossiê e Histórico":
            st.subheader("📊 Inteligência de Prazos e Histórico")
            for f in funcionarios_db:
                # Cálculo de Vencimento (Projeção de 23 meses a partir da admissão)
                data_limite = f['data_admissao'] + datetime.timedelta(days=700) 
                hoje = datetime.date.today()
                dias_restantes = (data_limite - hoje).days
                meses_restantes = dias_restantes // 30
                
                with st.expander(f"👤 {f['nome']} (Admissão: {f['data_admissao'].strftime('%d/%m/%Y')})"):
                    c1, c2 = st.columns([1, 2])
                    
                    # Alerta Visual do Contador
                    if dias_restantes < 90:
                        c1.error(f"🚨 CRÍTICO: {meses_restantes} meses para multa!")
                    elif dias_restantes < 180:
                        c1.warning(f"⚠️ ATENÇÃO: {meses_restantes} meses restantes.")
                    else:
                        c1.success(f"✅ Seguro: {meses_restantes} meses para o limite.")
                    
                    # Tabela de Histórico Real
                    hist = query_banco(f"SELECT data_inicio, data_fim, dias_corridos, status FROM rh_movimentacao_ferias WHERE id_funcionario={f['id_funcionario']} ORDER BY data_inicio DESC")
                    if hist:
                        c2.dataframe(pd.DataFrame(hist), use_container_width=True)
                    else:
                        c2.info("Nenhum histórico de férias registrado.")

        # 4.3 GERENCIAR EQUIPE E SEGURANÇA
        elif menu == "Gerenciar Equipe":
            st.subheader("⚙️ Configurações e Manutenção")
            
            with st.expander("➕ Admitir Novo Funcionário"):
                with st.form("cad_novo"):
                    n_nome = st.text_input("Nome").replace("'", "''")
                    n_adm = st.date_input("Admissão")
                    if st.form_submit_button("Cadastrar"):
                        query_banco(f"INSERT INTO rh_funcionarios (nome, data_admissao, is_ativo) VALUES ('{n_nome}', '{n_adm}', True)")
                        st.rerun()

            st.divider()
            for f in funcionarios_db:
                with st.container():
                    col_n, col_v, col_ip, col_del = st.columns([2, 1, 1, 1])
                    col_n.write(f"**{f['nome']}**")
                    
                    # Controle Seletivo de Abono
                    venda_ativada = bool(f['pode_vender_ferias'])
                    if col_v.toggle("Abono", value=venda_ativada, key=f"v_{f['id_funcionario']}"):
                        if not venda_ativada:
                            query_banco(f"UPDATE rh_funcionarios SET pode_vender_ferias = 1 WHERE id_funcionario={f['id_funcionario']}")
                            st.rerun()
                    else:
                        if venda_ativada:
                            query_banco(f"UPDATE rh_funcionarios SET pode_vender_ferias = 0 WHERE id_funcionario={f['id_funcionario']}")
                            st.rerun()
                    
                    # Reset IP (Caso mude de máquina ou cabo)
                    if col_ip.button("Resetar IP", key=f"ip_{f['id_funcionario']}"):
                        query_banco(f"UPDATE rh_funcionarios SET ip_maquina=NULL WHERE id_funcionario={f['id_funcionario']}")
                        st.success("IP Resetado!")
                    
                    # Excluir com Cadeado de Segurança
                    if col_del.button("🗑️", key=f"del_{f['id_funcionario']}"):
                        st.error("Para excluir permanentemente, digite CONFIRMO:")
                        confirm = st.text_input("Confirmação", key=f"conf_{f['id_funcionario']}")
                        if confirm == "CONFIRMO":
                            query_banco(f"DELETE FROM rh_funcionarios WHERE id_funcionario={f['id_funcionario']}")
                            st.rerun()
                st.divider()

    elif senha_lider != "":
        st.error("Senha incorreta.")
