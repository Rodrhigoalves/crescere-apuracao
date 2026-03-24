def modulo_apuracao():
    # Cabeçalho limpo e direto
    st.markdown("<h2 style='text-align: center; color: #1E3A8A;'>Painel de Apuração - PIS e COFINS</h2>", unsafe_allow_html=True)
    st.divider()

    conn = get_db_connection()
    
    # Validação inicial do banco
    try:
        df_empresas = pd.read_sql("SELECT id, nome, cnpj FROM empresas", conn)
        df_operacoes = pd.read_sql("SELECT * FROM operacoes", conn)
    except:
        st.error("⚠️ As tabelas base não foram encontradas.")
        with st.expander("⚙️ Ferramentas de Sistema (Setup Inicial)"):
            if st.button("🚨 Inicializar Tabelas de Apuração"):
                resetar_tabelas_apuracao()
        conn.close()
        return

    if df_empresas.empty:
        st.warning("Nenhuma empresa cadastrada no sistema.")
        conn.close()
        return

    # --- BARRA SUPERIOR DE FILTROS ---
    col_filtro1, col_filtro2, col_filtro3 = st.columns([2, 1, 1])
    
    opcoes_empresas = df_empresas.apply(lambda row: f"{row['nome']} - {row['cnpj']}", axis=1)
    empresa_selecionada = col_filtro1.selectbox("Empresa Ativa", opcoes_empresas, label_visibility="collapsed")
    empresa_id = df_empresas.loc[opcoes_empresas == empresa_selecionada, 'id'].values[0]
    
    # Pega o mês atual como padrão, mas permite alterar
    competencia = col_filtro2.text_input("Competência", value=date.today().strftime("%m/%Y"), help="Formato MM/AAAA")
    
    # Formata a competência para o banco de dados (AAAA-MM)
    try:
        mes_str, ano_str = competencia.split('/')
        competencia_db = f"{ano_str}-{mes_str.zfill(2)}"
    except:
        competencia_db = ""

    # --- BUSCA DE DADOS DA COMPETÊNCIA ---
    df_lancamentos = pd.DataFrame()
    if competencia_db:
        query = f"""
            SELECT l.data_lancamento, o.nome as operacao, l.valor_base, l.valor_pis, l.valor_cofins 
            FROM lancamentos l
            JOIN operacoes o ON l.operacao_id = o.id
            WHERE l.empresa_id = {empresa_id} AND l.competencia = '{competencia_db}'
            ORDER BY l.data_lancamento DESC, l.id DESC
        """
        try:
            df_lancamentos = pd.read_sql(query, conn)
        except:
            pass # Tabela pode estar vazia no reset

    # --- CARDS DE MÉTRICAS (VISUAL EXECUTIVO) ---
    st.write("") # Espaçamento
    m1, m2, m3 = st.columns(3)
    
    total_base = df_lancamentos['valor_base'].sum() if not df_lancamentos.empty else 0.0
    total_pis = df_lancamentos['valor_pis'].sum() if not df_lancamentos.empty else 0.0
    total_cofins = df_lancamentos['valor_cofins'].sum() if not df_lancamentos.empty else 0.0

    m1.metric("Base de Cálculo Total", f"R$ {total_base:,.2f}".replace(",", "X").replace(".", ",").replace("X", "."))
    m2.metric("PIS Apurado", f"R$ {total_pis:,.2f}".replace(",", "X").replace(".", ",").replace("X", "."))
    m3.metric("COFINS Apurado", f"R$ {total_cofins:,.2f}".replace(",", "X").replace(".", ",").replace("X", "."))
    st.divider()

    # --- LAYOUT DIVIDIDO: FORMULÁRIO (ESQUERDA) | EXTRATO (DIREITA) ---
    col_form, col_extrato = st.columns([1, 1.5], gap="large")

    with col_form:
        st.markdown("#### 📥 Novo Lançamento")
        with st.form("form_novo_lancamento", clear_on_submit=True):
            operacao_nome = st.selectbox("Tipo de Operação", df_operacoes['nome'].tolist())
            valor_base = st.number_input("Valor da Base (R$)", min_value=0.01, step=100.00, format="%.2f")
            historico = st.text_input("Histórico / Observação", placeholder="Ex: NF 1234 a 1250...")
            
            submit = st.form_submit_button("Registrar Valor", use_container_width=True)
            
            if submit:
                if not competencia_db:
                    st.error("Formato de competência inválido. Use MM/AAAA.")
                else:
                    op_data = df_operacoes[df_operacoes['nome'] == operacao_nome].iloc[0]
                    op_id = int(op_data['id'])
                    
                    valor_pis = valor_base * float(op_data['aliquota_pis'])
                    valor_cofins = valor_base * float(op_data['aliquota_cofins'])
                    
                    mes, ano = map(int, competencia.split('/'))
                    ultimo_dia = calendar.monthrange(ano, mes)[1]
                    data_lancamento = f"{ano}-{mes:02d}-{ultimo_dia:02d}"
                    
                    cursor = conn.cursor()
                    sql = """INSERT INTO lancamentos 
                             (empresa_id, operacao_id, competencia, data_lancamento, valor_base, valor_pis, valor_cofins, historico) 
                             VALUES (%s, %s, %s, %s, %s, %s, %s, %s)"""
                    cursor.execute(sql, (int(empresa_id), op_id, competencia_db, data_lancamento, valor_base, valor_pis, valor_cofins, historico))
                    conn.commit()
                    st.rerun() # Atualiza a tela imediatamente para mostrar no extrato

    with col_extrato:
        st.markdown(f"#### 📄 Extrato da Competência ({competencia})")
        if not df_lancamentos.empty:
            # Formatação para exibição no estilo contábil brasileiro
            df_view = df_lancamentos.copy()
            df_view['data_lancamento'] = pd.to_datetime(df_view['data_lancamento']).dt.strftime('%d/%m/%Y')
            df_view.rename(columns={
                'data_lancamento': 'Data',
                'operacao': 'Operação',
                'valor_base': 'Base (R$)',
                'valor_pis': 'PIS (R$)',
                'valor_cofins': 'COFINS (R$)'
            }, inplace=True)
            
            st.dataframe(df_view, use_container_width=True, hide_index=True)
        else:
            st.info("Nenhum lançamento registrado para esta empresa nesta competência.")

    conn.close()
