def testar_fluxo_basico():
    db = Database()
    auditoria = AuditoriaService(db)
    fechamento_service = FechamentoService(db, auditoria)
    lanc_service = LancamentoService(db, auditoria, fechamento_service)
    custo_service = CustoService(db, auditoria)
    relatorio_service = RelatorioService(db)
    auth_service = AuthService(db)

    user = auth_service.autenticar("admin", "admin123")
    if not user:
        db.close()
        raise RuntimeError("Não foi possível autenticar admin/admin123")

    usuario_id = user["id"]

    empresa = db.execute("SELECT id FROM empresas LIMIT 1").fetchone()
    if not empresa:
        db.close()
        raise RuntimeError("Nenhuma empresa cadastrada.")

    empresa_id = empresa["id"]

    operacoes = db.execute("SELECT id, nome FROM operacoes ORDER BY id").fetchall()
    mapa_ops = {o["nome"]: o["id"] for o in operacoes}

    competencia_teste = "2026-03"
    competencia_retroativa = "2026-01"

    # Limpeza para permitir rodar várias vezes
    db.execute(
        "DELETE FROM custos WHERE empresa_id = ? AND competencia = ?",
        (empresa_id, competencia_teste)
    )
    db.execute(
        "DELETE FROM lancamentos WHERE empresa_id = ? AND competencia IN (?, ?)",
        (empresa_id, competencia_retroativa, competencia_teste)
    )
    db.execute(
        "DELETE FROM fechamentos WHERE empresa_id = ? AND competencia = ?",
        (empresa_id, competencia_teste)
    )
    db.commit()

    motivo_teste = "Rotina automática de teste"

    print("\n--- Lançando débito normal ---")
    lanc1 = lanc_service.criar_lancamento(
        empresa_id=empresa_id,
        operacao_id=mapa_ops["Venda de Serviços"],
        competencia=competencia_teste,
        valor_base=150000.00,
        observacao="Venda de serviços março",
        usuario_id=usuario_id,
        motivo_edicao_mes_fechado=motivo_teste
    )
    print(f"Lançamento criado: {lanc1}")

    print("\n--- Lançando receita financeira ---")
    lanc2 = lanc_service.criar_lancamento(
        empresa_id=empresa_id,
        operacao_id=mapa_ops["Receita Financeira"],
        competencia=competencia_teste,
        valor_base=10000.00,
        observacao="Receita financeira março",
        usuario_id=usuario_id,
        motivo_edicao_mes_fechado=motivo_teste
    )
    print(f"Lançamento criado: {lanc2}")

    print("\n--- Lançando crédito normal ---")
    lanc3 = lanc_service.criar_lancamento(
        empresa_id=empresa_id,
        operacao_id=mapa_ops["Compra Mercador/Insumos"],
        competencia=competencia_teste,
        valor_base=40000.00,
        observacao="Compra de insumos março",
        usuario_id=usuario_id,
        motivo_edicao_mes_fechado=motivo_teste
    )
    print(f"Lançamento criado: {lanc3}")

    print("\n--- Lançando crédito retroativo de janeiro apresentado em março ---")
    lanc4 = lanc_service.criar_lancamento(
        empresa_id=empresa_id,
        operacao_id=mapa_ops["Compra Mercador/Insumos"],
        competencia=competencia_retroativa,
        valor_base=12000.00,
        observacao="NF janeiro apresentada em março",
        usuario_id=usuario_id,
        origem_retroativa=1,
        competencia_origem=competencia_retroativa,
        data_apresentacao="2026-03-20",
        motivo_edicao_mes_fechado=motivo_teste
    )
    print(f"Lançamento retroativo criado: {lanc4}")

    print("\n--- Calculando custo ---")
    custo_id = custo_service.calcular_e_registrar_custo(
        empresa_id=empresa_id,
        competencia=competencia_teste,
        valor_bruto=50000.00,
        observacao="Cálculo de custo do mês",
        usuario_id=usuario_id
    )
    print(f"Custo registrado: {custo_id}")

    print("\n--- Fechando competência ---")
    fechamento_id = fechamento_service.fechar_competencia(
        empresa_id=empresa_id,
        competencia=competencia_teste,
        usuario_id=usuario_id,
        observacao="Fechamento inicial de teste"
    )
    print(f"Fechamento realizado: {fechamento_id}")

    print("\n--- Resumo da competência ---")
    resumo = relatorio_service.resumo_competencia(empresa_id, competencia_teste)
    print(json.dumps(resumo, indent=2, ensure_ascii=False, default=str))

    db.close()
