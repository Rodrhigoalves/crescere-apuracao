import streamlit as st
import sys
import os
import datetime

# 1. CONEXÃO E CAMINHOS
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))
try:
    from database import query_banco
except ImportError:
    st.error("Erro: O arquivo 'database.py' não foi encontrado na raiz.")
    st.stop()

# 2. FUNÇÃO PARA CAPTURAR IP
def get_remote_ip():
    # Tenta capturar o IP real do usuário (funciona no Streamlit Cloud e Local)
    try:
        # No Streamlit Cloud, o IP vem no cabeçalho X-Forwarded-For
        ip = st.context.headers.get("X-Forwarded-For", "127.0.0.1").split(',')[0]
        return ip
    except:
        return "127.0.0.1"

# 3. CONFIGURAÇÕES INICIAIS
st.set_page_config(page_title="Gestão de Férias", layout="wide")
current_ip = get_remote_ip()

st.title("📅 Sistema de Controle de Férias")
st.caption(f"Seu endereço IP atual: {current_ip}")

# Busca dados atualizados
try:
    funcionarios_db = query_banco("SELECT * FROM rh_funcionarios WHERE is_ativo = True ORDER BY nome ASC")
except:
    st.error("Erro ao carregar banco de dados. Verifique se as colunas ip_maquina e pode_vender_ferias foram criadas.")
    st.stop()

tab_func, tab_lider = st.tabs(["👤 Espaço do Funcionário", "🔒 Área Restrita (Líder)"])

# --- 4. ESPAÇO DO FUNCIONÁRIO (TRAVA POR IP) ---
with tab_func:
    if not funcionarios_db:
        st.info("Nenhum funcionário cadastrado.")
    else:
        nomes = [f['nome'] for f in funcionarios_db]
        selecionado = st.selectbox("Selecione seu nome para acessar seu painel:", [""] + nomes)

        if selecionado:
            user = next(item for item in funcionarios_db if item["nome"] == selecionado)
            
            # TRAVA DE PRIVACIDADE: Compara o IP da máquina com o IP cadastrado pelo Líder
            if user['ip_maquina'] != current_ip:
                st.error("🚫 ACESSO NEGADO: Máquina não autorizada.")
                st.warning(f"O colaborador **{selecionado}** está vinculado a outro endereço IP. Se você mudou de máquina ou de cabo de rede, solicite ao seu líder a atualização do seu IP de acesso.")
                st.info(f"IP desta máquina: {current_ip}")
            
            elif not user['email_corporativo']:
                st.error("Acesso Bloqueado: E-mail corporativo não identificado.")
            
            else:
                st.success(f"Identidade validada via Protocolo de IP ({current_ip})")
                with st.form("solicitacao_ferias", clear_on_submit=True):
                    col1, col2 = st.columns(2)
                    d_ini = col1.date_input("Data de Início")
                    d_fim = col2.date_input("Data de Término")
                    
                    # Regra do Abono: Só aparece se o líder habilitou para este funcionário
                    permite_venda = bool(user['pode_vender_ferias'])
                    if permite_venda:
                        abono = st.checkbox("Desejo vender 10 dias (Abono Pecuniário)")
                    else:
                        abono = False
                        st.info("Opção de venda de férias não disponível para seu perfil.")

                    if st.form_submit_button("Enviar Solicitação Oficial"):
                        total_dias = (d_fim - d_ini).days + 1
                        if total_dias < 5:
                            st.error("Erro: O período mínimo deve ser de 5 dias.")
                        else:
                            # Grava IP e Horário no Protocolo
                            sql_sol = f"""
                                INSERT INTO rh_movimentacao_ferias 
                                (id_funcionario, data_inicio, data_fim, dias_corridos, abono_pecuniario, ip_registro, status)
                                VALUES ({user['id_funcionario']}, '{d_ini}', '{d_fim}', {total_dias}, {abono}, '{current_ip}', 'Pendente')
                            """
                            query_banco(sql_sol)
                            st.success(f"✅ Solicitação enviada! Registrada sob o IP {current_ip} em {datetime.datetime.now().strftime('%d/%m/%Y %H:%M')}")

# --- 5. ÁREA RESTRITA (LÍDER) ---
with tab_lider:
    senha_lider = st.text_input("Senha de Gestão:", type="password")
    
    if senha_lider == "123": # <--- Altere sua senha aqui
        st.divider()
        menu_lider = st.radio("Selecione a tarefa:", ["Aprovações", "Gestão de Equipe", "Vínculo de IPs"], horizontal=True)

        if menu_lider == "Aprovações":
            pedidos = query_banco("""
                SELECT f.nome, m.* FROM rh_movimentacao_ferias m 
                JOIN rh_funcionarios f ON m.id_funcionario = f.id_funcionario 
                WHERE m.status = 'Pendente'
            """)
            if not pedidos:
                st.info("Sem pendências.")
            else:
                for p in pedidos:
                    with st.expander(f"Solicitação: {p['nome']} (IP de Origem: {p['ip_registro']})"):
                        st.write(f"**Período:** {p['data_inicio'].strftime('%d/%m/%Y')} a {p['data_fim'].strftime('%d/%m/%Y')} ({p['dias_corridos']} dias)")
                        st.write(f"**Abono:** {'Sim' if p['abono_pecuniario'] else 'Não'}")
                        
                        ca, cr = st.columns(2)
                        if ca.button("✅ Aprovar", key=f"aprova_{p['id_movimento']}"):
                            query_banco(f"UPDATE rh_movimentacao_ferias SET status='Aprovado' WHERE id_movimento={p['id_movimento']}")
                            st.rerun()
                        if cr.button("❌ Recusar", key=f"recusa_{p['id_movimento']}"):
                            query_banco(f"UPDATE rh_movimentacao_ferias SET status='Recusado' WHERE id_movimento={p['id_movimento']}")
                            st.rerun()

        elif menu_lider == "Gestão de Equipe":
            # CADASTRO E CONTROLE DE ABONO
            with st.expander("➕ Admitir Novo Funcionário"):
                with st.form("cad_novo"):
                    n_nome = st.text_input("Nome").replace("'", "''")
                    n_adm = st.date_input("Admissão")
                    if st.form_submit_button("Cadastrar"):
                        query_banco(f"INSERT INTO rh_funcionarios (nome, data_admissao, is_ativo) VALUES ('{n_nome}', '{n_adm}', True)")
                        st.rerun()

            st.write("### Lista de Colaboradores")
            for f in funcionarios_db:
                with st.container():
                    c1, c2, c3 = st.columns([2, 1, 1])
                    c1.write(f"**{f['nome']}**")
                    
                    # Toggle para o líder permitir abono
                    valor_abono = bool(f['pode_vender_ferias'])
                    novo_venda = c2.toggle("Permitir Abono", value=valor_abono, key=f"tgl_{f['id_funcionario']}")
                    if novo_venda != valor_abono:
                        query_banco(f"UPDATE rh_funcionarios SET pode_vender_ferias={novo_venda} WHERE id_funcionario={f['id_funcionario']}")
                        st.rerun()

                    # CADEADO DE EXCLUSÃO
                    if c3.button("🗑️ Excluir", key=f"del_{f['id_funcionario']}"):
                        st.error(f"ATENÇÃO: Para excluir permanentemente {f['nome']}, digite CONFIRMO abaixo:")
                        confirmacao = st.text_input("Digite aqui:", key=f"confirm_{f['id_funcionario']}")
                        if confirmacao == "CONFIRMO":
                            query_banco(f"DELETE FROM rh_funcionarios WHERE id_funcionario={f['id_funcionario']}")
                            st.success("Excluído!")
                            st.rerun()
                st.divider()

        elif menu_lider == "Vínculo de IPs":
            st.subheader("Configuração de Segurança por Máquina")
            st.info(f"O IP da sua máquina atual é: **{current_ip}**")
            
            for f in funcionarios_db:
                col_n, col_i, col_b = st.columns([2, 2, 1])
                col_n.write(f['nome'])
                ip_input = col_i.text_input("IP da Máquina Fixa", value=f['ip_maquina'] or "", key=f"ipinput_{f['id_funcionario']}")
                if col_b.button("Salvar IP", key=f"btnsaveip_{f['id_funcionario']}"):
                    query_banco(f"UPDATE rh_funcionarios SET ip_maquina='{ip_input}' WHERE id_funcionario={f['id_funcionario']}")
                    st.success(f"IP vinculado a {f['nome']}!")
    
    elif senha_lider != "":
        st.error("Senha incorreta.")
