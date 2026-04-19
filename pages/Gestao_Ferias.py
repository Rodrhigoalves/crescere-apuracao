# Exemplo de lógica para o portal do funcionário
funcionario = query_banco("SELECT email_corporativo FROM rh_funcionarios WHERE id=X")

if funcionario['email_corporativo'] is None:
    st.error("Acesso Bloqueado: Solicite ao seu líder o cadastro do seu e-mail corporativo.")
else:
    # Mostra formulário de solicitação de férias
    render_formulario_ferias()
