import streamlit as st
from database import query_banco # Supondo que sua conexão está separada

st.title("📅 Gestão de Férias")

# Aqui você precisará de uma forma de saber quem é o funcionário logado.
# Pode ser por um st.selectbox inicial para teste ou um sistema de login.
nome_usuario = st.sidebar.selectbox("Selecione seu nome para acessar:", lista_de_nomes)

# Busca os dados no banco usando as tabelas novas que criamos
dados = query_banco(f"SELECT email_corporativo, is_ativo FROM rh_funcionarios WHERE nome = '{nome_usuario}'")

if not dados:
    st.warning("Usuário não encontrado no sistema de RH.")
elif not dados[0]['is_ativo']:
    st.error("Acesso bloqueado: Colaborador desligado.")
elif dados[0]['email_corporativo'] is None:
    st.error("🚫 Acesso Bloqueado: Solicite ao seu líder o cadastro do seu e-mail corporativo.")
    st.info("O agendamento via sistema só é liberado após a vinculação do e-mail oficial.")
else:
    # Se passar por todos os filtros, mostra o formulário
    st.success(f"Bem-vindo, {nome_usuario}! Utilize o formulário abaixo.")
    render_formulario_ferias()
