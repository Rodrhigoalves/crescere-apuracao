import sqlite3
import hashlib
import os
import json
from datetime import datetime, date
from typing import Optional, Dict, Any, List, Tuple

DB_PATH = "apuracao_piscofins.db"
DIA_CORTE_RETROATIVO = 25


# =========================================================
# UTILITÁRIOS
# =========================================================

def agora_str() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def hoje_str() -> str:
    return date.today().strftime("%Y-%m-%d")


def json_dumps(data: Any) -> str:
    return json.dumps(data, ensure_ascii=False, default=str)


def gerar_salt() -> str:
    return os.urandom(16).hex()


def hash_senha(senha: str, salt: str) -> str:
    return hashlib.sha256((senha + salt).encode("utf-8")).hexdigest()


def normalizar_competencia(comp: str) -> str:
    """
    Aceita:
    - YYYY-MM
    - MM/YYYY

    Retorna sempre YYYY-MM
    """
    comp = comp.strip()

    if "/" in comp:
        mes, ano = comp.split("/")
        mes = mes.zfill(2)
        if len(ano) != 4:
            raise ValueError(f"Competência inválida: {comp}")
        return f"{ano}-{mes}"

    if "-" in comp and len(comp) == 7:
        ano, mes = comp.split("-")
        if len(ano) != 4:
            raise ValueError(f"Competência inválida: {comp}")
        return f"{ano}-{mes.zfill(2)}"

    raise ValueError(f"Competência inválida: {comp}")


def competencia_anterior(comp: str) -> str:
    comp = normalizar_competencia(comp)
    ano, mes = map(int, comp.split("-"))

    if mes == 1:
        return f"{ano - 1}-12"
    return f"{ano}-{str(mes - 1).zfill(2)}"


def competencia_posterior(comp: str) -> str:
    comp = normalizar_competencia(comp)
    ano, mes = map(int, comp.split("-"))

    if mes == 12:
        return f"{ano + 1}-01"
    return f"{ano}-{str(mes + 1).zfill(2)}"


def parse_data(data_str: str) -> date:
    return datetime.strptime(data_str, "%Y-%m-%d").date()


def to_dict(row: Optional[sqlite3.Row]) -> Optional[Dict[str, Any]]:
    if row is None:
        return None
    return dict(row)


# =========================================================
# BANCO DE DADOS
# =========================================================

class Database:
    def __init__(self, db_path: str = DB_PATH):
        self.db_path = db_path
        self.conn = sqlite3.connect(self.db_path)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA foreign_keys = ON")

    def execute(self, sql: str, params: Tuple = ()) -> sqlite3.Cursor:
        cur = self.conn.cursor()
        cur.execute(sql, params)
        return cur

    def executemany(self, sql: str, seq_of_params):
        cur = self.conn.cursor()
        cur.executemany(sql, seq_of_params)
        return cur

    def commit(self):
        self.conn.commit()

    def rollback(self):
        self.conn.rollback()

    def close(self):
        self.conn.close()

    def table_exists(self, table_name: str) -> bool:
        cur = self.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name=?",
            (table_name,)
        )
        return cur.fetchone() is not None

    def get_columns(self, table_name: str) -> List[str]:
        if not self.table_exists(table_name):
            return []
        cur = self.execute(f"PRAGMA table_info({table_name})")
        return [row["name"] for row in cur.fetchall()]

    def column_exists(self, table_name: str, column_name: str) -> bool:
        return column_name in self.get_columns(table_name)

    def add_column_if_not_exists(self, table_name: str, column_definition: str):
        col_name = column_definition.split()[0]
        if not self.column_exists(table_name, col_name):
            self.execute(f"ALTER TABLE {table_name} ADD COLUMN {column_definition}")
            self.commit()

    def create_index_if_not_exists(self, index_name: str, table_name: str, columns: str):
        self.execute(f"CREATE INDEX IF NOT EXISTS {index_name} ON {table_name} ({columns})")
        self.commit()


# =========================================================
# MIGRAÇÃO INCREMENTAL SEGURA
# =========================================================

class Migrator:
    def __init__(self, db: Database):
        self.db = db

    def migrate(self):
        self._migrar_usuarios()
        self._migrar_empresas()
        self._migrar_operacoes()
        self._migrar_mapeamentos_erp()
        self._migrar_lancamentos()
        self._migrar_fechamentos()
        self._migrar_auditoria()
        self._migrar_custos()
        self._criar_indices()

    def _migrar_usuarios(self):
        if not self.db.table_exists("usuarios"):
            self.db.execute("""
                CREATE TABLE usuarios (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    username TEXT UNIQUE NOT NULL,
                    nome TEXT,
                    password_hash TEXT NOT NULL,
                    salt TEXT NOT NULL,
                    perfil TEXT NOT NULL DEFAULT 'operador',
                    ativo INTEGER NOT NULL DEFAULT 1,
                    created_at TEXT,
                    updated_at TEXT
                )
            """)
            self.db.commit()
        else:
            cols = [
                "nome TEXT",
                "password_hash TEXT",
                "salt TEXT",
                "perfil TEXT DEFAULT 'operador'",
                "ativo INTEGER NOT NULL DEFAULT 1",
                "created_at TEXT",
                "updated_at TEXT",
            ]
            for c in cols:
                self.db.add_column_if_not_exists("usuarios", c)

    def _migrar_empresas(self):
        if not self.db.table_exists("empresas"):
            self.db.execute("""
                CREATE TABLE empresas (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    nome TEXT NOT NULL,
                    cnpj TEXT,
                    regime_tributario TEXT DEFAULT 'Lucro Real',
                    tipo_empresa TEXT,
                    tipo_estabelecimento TEXT,
                    grupo_empresarial TEXT,
                    empresa_matriz_id INTEGER,
                    cnae TEXT,
                    descricao_cnae TEXT,
                    ativo INTEGER NOT NULL DEFAULT 1,
                    created_at TEXT,
                    updated_at TEXT,
                    FOREIGN KEY (empresa_matriz_id) REFERENCES empresas(id)
                )
            """)
            self.db.commit()
        else:
            cols = [
                "cnpj TEXT",
                "regime_tributario TEXT DEFAULT 'Lucro Real'",
                "tipo_empresa TEXT",
                "tipo_estabelecimento TEXT",
                "grupo_empresarial TEXT",
                "empresa_matriz_id INTEGER",
                "cnae TEXT",
                "descricao_cnae TEXT",
                "ativo INTEGER NOT NULL DEFAULT 1",
                "created_at TEXT",
                "updated_at TEXT",
            ]
            for c in cols:
                self.db.add_column_if_not_exists("empresas", c)

    def _migrar_operacoes(self):
        if not self.db.table_exists("operacoes"):
            self.db.execute("""
                CREATE TABLE operacoes (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    nome TEXT NOT NULL,
                    natureza TEXT NOT NULL,
                    categoria_fiscal TEXT,
                    receita_financeira INTEGER NOT NULL DEFAULT 0,
                    ativo INTEGER NOT NULL DEFAULT 1,
                    created_at TEXT,
                    updated_at TEXT
                )
            """)
            self.db.commit()
        else:
            cols = [
                "nome TEXT",
                "natureza TEXT",
                "categoria_fiscal TEXT",
                "receita_financeira INTEGER NOT NULL DEFAULT 0",
                "ativo INTEGER NOT NULL DEFAULT 1",
                "created_at TEXT",
                "updated_at TEXT",
            ]
            for c in cols:
                self.db.add_column_if_not_exists("operacoes", c)

    def _migrar_mapeamentos_erp(self):
        if not self.db.table_exists("mapeamentos_erp"):
            self.db.execute("""
                CREATE TABLE mapeamentos_erp (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    tipo_lancamento TEXT NOT NULL,
                    conta_debito TEXT,
                    conta_credito TEXT,
                    historico_base TEXT,
                    observacoes TEXT,
                    ativo INTEGER NOT NULL DEFAULT 1,
                    created_at TEXT,
                    updated_at TEXT
                )
            """)
            self.db.commit()
        else:
            cols = [
                "tipo_lancamento TEXT",
                "conta_debito TEXT",
                "conta_credito TEXT",
                "historico_base TEXT",
                "observacoes TEXT",
                "ativo INTEGER NOT NULL DEFAULT 1",
                "created_at TEXT",
                "updated_at TEXT",
            ]
            for c in cols:
                self.db.add_column_if_not_exists("mapeamentos_erp", c)

    def _migrar_lancamentos(self):
        if not self.db.table_exists("lancamentos"):
            self.db.execute("""
                CREATE TABLE lancamentos (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    empresa_id INTEGER NOT NULL,
                    operacao_id INTEGER NOT NULL,
                    competencia TEXT NOT NULL,
                    valor_base REAL NOT NULL,
                    natureza TEXT NOT NULL,
                    pis_individual REAL NOT NULL DEFAULT 0,
                    cofins_individual REAL NOT NULL DEFAULT 0,
                    observacao TEXT,
                    origem_retroativa INTEGER NOT NULL DEFAULT 0,
                    competencia_origem TEXT,
                    data_apresentacao TEXT,
                    competencia_aproveitamento TEXT,
                    aproveitado_em_competencia TEXT,
                    status TEXT DEFAULT 'disponivel',
                    created_by INTEGER,
                    created_at TEXT,
                    updated_by INTEGER,
                    updated_at TEXT,
                    FOREIGN KEY (empresa_id) REFERENCES empresas(id),
                    FOREIGN KEY (operacao_id) REFERENCES operacoes(id),
                    FOREIGN KEY (created_by) REFERENCES usuarios(id),
                    FOREIGN KEY (updated_by) REFERENCES usuarios(id)
                )
            """)
            self.db.commit()
        else:
            cols = [
                "empresa_id INTEGER",
                "operacao_id INTEGER",
                "competencia TEXT",
                "valor_base REAL NOT NULL DEFAULT 0",
                "natureza TEXT",
                "pis_individual REAL NOT NULL DEFAULT 0",
                "cofins_individual REAL NOT NULL DEFAULT 0",
                "observacao TEXT",
                "origem_retroativa INTEGER NOT NULL DEFAULT 0",
                "competencia_origem TEXT",
                "data_apresentacao TEXT",
                "competencia_aproveitamento TEXT",
                "aproveitado_em_competencia TEXT",
                "status TEXT DEFAULT 'disponivel'",
                "created_by INTEGER",
                "created_at TEXT",
                "updated_by INTEGER",
                "updated_at TEXT",
            ]
            for c in cols:
                self.db.add_column_if_not_exists("lancamentos", c)

    def _migrar_fechamentos(self):
        if not self.db.table_exists("fechamentos"):
            self.db.execute("""
                CREATE TABLE fechamentos (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    empresa_id INTEGER NOT NULL,
                    competencia TEXT NOT NULL,
                    total_debito_pis REAL NOT NULL DEFAULT 0,
                    total_credito_pis REAL NOT NULL DEFAULT 0,
                    saldo_anterior_pis REAL NOT NULL DEFAULT 0,
                    credito_retroativo_pis REAL NOT NULL DEFAULT 0,
                    resultado_pis REAL NOT NULL DEFAULT 0,
                    saldo_transportar_pis REAL NOT NULL DEFAULT 0,
                    total_debito_cofins REAL NOT NULL DEFAULT 0,
                    total_credito_cofins REAL NOT NULL DEFAULT 0,
                    saldo_anterior_cofins REAL NOT NULL DEFAULT 0,
                    credito_retroativo_cofins REAL NOT NULL DEFAULT 0,
                    resultado_cofins REAL NOT NULL DEFAULT 0,
                    saldo_transportar_cofins REAL NOT NULL DEFAULT 0,
                    status TEXT NOT NULL DEFAULT 'aberto',
                    observacao TEXT,
                    fechado_por INTEGER,
                    fechado_em TEXT,
                    reaberto_por INTEGER,
                    reaberto_em TEXT,
                    created_at TEXT,
                    updated_at TEXT,
                    FOREIGN KEY (empresa_id) REFERENCES empresas(id),
                    FOREIGN KEY (fechado_por) REFERENCES usuarios(id),
                    FOREIGN KEY (reaberto_por) REFERENCES usuarios(id),
                    UNIQUE (empresa_id, competencia)
                )
            """)
            self.db.commit()
        else:
            cols = [
                "total_debito_pis REAL NOT NULL DEFAULT 0",
                "total_credito_pis REAL NOT NULL DEFAULT 0",
                "saldo_anterior_pis REAL NOT NULL DEFAULT 0",
                "credito_retroativo_pis REAL NOT NULL DEFAULT 0",
                "resultado_pis REAL NOT NULL DEFAULT 0",
                "saldo_transportar_pis REAL NOT NULL DEFAULT 0",
                "total_debito_cofins REAL NOT NULL DEFAULT 0",
                "total_credito_cofins REAL NOT NULL DEFAULT 0",
                "saldo_anterior_cofins REAL NOT NULL DEFAULT 0",
                "credito_retroativo_cofins REAL NOT NULL DEFAULT 0",
                "resultado_cofins REAL NOT NULL DEFAULT 0",
                "saldo_transportar_cofins REAL NOT NULL DEFAULT 0",
                "status TEXT NOT NULL DEFAULT 'aberto'",
                "observacao TEXT",
                "fechado_por INTEGER",
                "fechado_em TEXT",
                "reaberto_por INTEGER",
                "reaberto_em TEXT",
                "created_at TEXT",
                "updated_at TEXT",
            ]
            for c in cols:
                self.db.add_column_if_not_exists("fechamentos", c)

    def _migrar_auditoria(self):
        if not self.db.table_exists("auditoria"):
            self.db.execute("""
                CREATE TABLE auditoria (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    usuario_id INTEGER,
                    acao TEXT NOT NULL,
                    entidade TEXT NOT NULL,
                    registro_id INTEGER,
                    competencia TEXT,
                    motivo TEXT,
                    antes_json TEXT,
                    depois_json TEXT,
                    timestamp TEXT NOT NULL,
                    FOREIGN KEY (usuario_id) REFERENCES usuarios(id)
                )
            """)
            self.db.commit()

    def _migrar_custos(self):
        if not self.db.table_exists("custos"):
            self.db.execute("""
                CREATE TABLE custos (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    empresa_id INTEGER NOT NULL,
                    competencia TEXT NOT NULL,
                    tipo_custo TEXT NOT NULL,
                    valor_bruto REAL NOT NULL,
                    pis_custo REAL NOT NULL DEFAULT 0,
                    cofins_custo REAL NOT NULL DEFAULT 0,
                    valor_liquido REAL NOT NULL DEFAULT 0,
                    observacao TEXT,
                    created_by INTEGER,
                    created_at TEXT,
                    updated_by INTEGER,
                    updated_at TEXT,
                    FOREIGN KEY (empresa_id) REFERENCES empresas(id),
                    FOREIGN KEY (created_by) REFERENCES usuarios(id),
                    FOREIGN KEY (updated_by) REFERENCES usuarios(id)
                )
            """)
            self.db.commit()

    def _criar_indices(self):
        self.db.create_index_if_not_exists("idx_lanc_empresa_comp", "lancamentos", "empresa_id, competencia")
        self.db.create_index_if_not_exists("idx_lanc_comp_aprov", "lancamentos", "competencia_aproveitamento")
        self.db.create_index_if_not_exists("idx_fech_empresa_comp", "fechamentos", "empresa_id, competencia")
        self.db.create_index_if_not_exists("idx_auditoria_comp", "auditoria", "competencia")


# =========================================================
# AUDITORIA
# =========================================================

class AuditoriaService:
    def __init__(self, db: Database):
        self.db = db

    def registrar(
        self,
        usuario_id: Optional[int],
        acao: str,
        entidade: str,
        registro_id: Optional[int],
        competencia: Optional[str],
        motivo: Optional[str],
        antes: Optional[Dict[str, Any]],
        depois: Optional[Dict[str, Any]],
    ):
        self.db.execute("""
            INSERT INTO auditoria (
                usuario_id, acao, entidade, registro_id, competencia,
                motivo, antes_json, depois_json, timestamp
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            usuario_id,
            acao,
            entidade,
            registro_id,
            competencia,
            motivo,
            json_dumps(antes) if antes else None,
            json_dumps(depois) if depois else None,
            agora_str()
        ))
        self.db.commit()


# =========================================================
# AUTENTICAÇÃO
# =========================================================

class AuthService:
    def __init__(self, db: Database):
        self.db = db

    def buscar_usuario_por_username(self, username: str) -> Optional[sqlite3.Row]:
        cur = self.db.execute("SELECT * FROM usuarios WHERE username = ?", (username,))
        return cur.fetchone()

    def criar_usuario(self, username: str, senha: str, perfil: str = "operador", nome: str = "") -> int:
        if perfil not in ("admin", "operador", "consulta"):
            raise ValueError("Perfil inválido.")

        salt = gerar_salt()
        senha_hash = hash_senha(senha, salt)

        cur = self.db.execute("""
            INSERT INTO usuarios (
                username, nome, password_hash, salt, perfil, ativo, created_at, updated_at
            )
            VALUES (?, ?, ?, ?, ?, 1, ?, ?)
        """, (username, nome, senha_hash, salt, perfil, agora_str(), agora_str()))
        self.db.commit()
        return cur.lastrowid

    def autenticar(self, username: str, senha: str) -> Optional[sqlite3.Row]:
        user = self.buscar_usuario_por_username(username)
        if not user:
            return None
        if user["ativo"] != 1:
            return None
        senha_hash = hash_senha(senha, user["salt"])
        if senha_hash == user["password_hash"]:
            return user
        return None

    def garantir_admin_padrao(self):
        user = self.buscar_usuario_por_username("admin")
        if not user:
            self.criar_usuario("admin", "admin123", perfil="admin", nome="Administrador")
            print("Usuário admin criado com senha padrão: admin123")


# =========================================================
# EMPRESAS
# =========================================================

class EmpresaService:
    def __init__(self, db: Database):
        self.db = db

    def criar_empresa(
        self,
        nome: str,
        cnpj: str,
        regime_tributario: str,
        tipo_empresa: str,
        tipo_estabelecimento: str,
        grupo_empresarial: str = "",
        empresa_matriz_id: Optional[int] = None,
        cnae: str = "",
        descricao_cnae: str = "",
        ativo: int = 1
    ) -> int:
        if tipo_empresa not in ("comercio", "servico", "industria"):
            raise ValueError("tipo_empresa inválido.")
        if tipo_estabelecimento not in ("matriz", "filial"):
            raise ValueError("tipo_estabelecimento inválido.")

        cur = self.db.execute("""
            INSERT INTO empresas (
                nome, cnpj, regime_tributario, tipo_empresa, tipo_estabelecimento,
                grupo_empresarial, empresa_matriz_id, cnae, descricao_cnae, ativo,
                created_at, updated_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            nome, cnpj, regime_tributario, tipo_empresa, tipo_estabelecimento,
            grupo_empresarial, empresa_matriz_id, cnae, descricao_cnae, ativo,
            agora_str(), agora_str()
        ))
        self.db.commit()
        return cur.lastrowid

    def buscar_empresa(self, empresa_id: int) -> Optional[sqlite3.Row]:
        cur = self.db.execute("SELECT * FROM empresas WHERE id = ?", (empresa_id,))
        return cur.fetchone()


# =========================================================
# OPERAÇÕES
# =========================================================

class OperacaoService:
    def __init__(self, db: Database):
        self.db = db

    def criar_operacao(
        self,
        nome: str,
        natureza: str,
        categoria_fiscal: str = "",
        receita_financeira: int = 0,
        ativo: int = 1
    ) -> int:
        if natureza not in ("debito", "credito"):
            raise ValueError("natureza inválida.")

        cur = self.db.execute("""
            INSERT INTO operacoes (
                nome, natureza, categoria_fiscal, receita_financeira, ativo, created_at, updated_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?)
        """, (
            nome, natureza, categoria_fiscal, receita_financeira,
            ativo, agora_str(), agora_str()
        ))
        self.db.commit()
        return cur.lastrowid

    def buscar_operacao(self, operacao_id: int) -> Optional[sqlite3.Row]:
        cur = self.db.execute("SELECT * FROM operacoes WHERE id = ?", (operacao_id,))
        return cur.fetchone()


# =========================================================
# MAPEAMENTO ERP
# =========================================================

class ERPService:
    def __init__(self, db: Database):
        self.db = db

    def criar_mapeamento(
        self,
        tipo_lancamento: str,
        conta_debito: str,
        conta_credito: str,
        historico_base: str,
        observacoes: str = ""
    ) -> int:
        cur = self.db.execute("""
            INSERT INTO mapeamentos_erp (
                tipo_lancamento, conta_debito, conta_credito, historico_base,
                observacoes, ativo, created_at, updated_at
            )
            VALUES (?, ?, ?, ?, ?, 1, ?, ?)
        """, (
            tipo_lancamento, conta_debito, conta_credito, historico_base,
            observacoes, agora_str(), agora_str()
        ))
        self.db.commit()
        return cur.lastrowid


# =========================================================
# REGRAS FISCAIS
# =========================================================

class RegraFiscalService:
    ALIQ_PIS = 0.0165
    ALIQ_COFINS = 0.076
    ALIQ_PIS_FIN = 0.0065
    ALIQ_COFINS_FIN = 0.04

    @classmethod
    def calcular_pis_cofins(cls, valor_base: float, receita_financeira: bool) -> Tuple[float, float]:
        if receita_financeira:
            return round(valor_base * cls.ALIQ_PIS_FIN, 2), round(valor_base * cls.ALIQ_COFINS_FIN, 2)
        return round(valor_base * cls.ALIQ_PIS, 2), round(valor_base * cls.ALIQ_COFINS, 2)

    @classmethod
    def determinar_competencia_aproveitamento(cls, data_apresentacao: str, dia_corte: int = DIA_CORTE_RETROATIVO) -> str:
        d = parse_data(data_apresentacao)
        comp_apresentacao = f"{d.year}-{str(d.month).zfill(2)}"

        if d.day < dia_corte:
            return competencia_anterior(comp_apresentacao)
        return comp_apresentacao


# =========================================================
# FECHAMENTOS
# =========================================================

class FechamentoService:
    def __init__(self, db: Database, auditoria: AuditoriaService):
        self.db = db
        self.auditoria = auditoria

    def buscar_fechamento(self, empresa_id: int, competencia: str) -> Optional[sqlite3.Row]:
        competencia = normalizar_competencia(competencia)
        cur = self.db.execute("""
            SELECT * FROM fechamentos
            WHERE empresa_id = ? AND competencia = ?
        """, (empresa_id, competencia))
        return cur.fetchone()

    def competencia_fechada(self, empresa_id: int, competencia: str) -> bool:
        fechamento = self.buscar_fechamento(empresa_id, competencia)
        if not fechamento:
            return False
        return fechamento["status"] == "fechado"

    def saldo_transportado_anterior(self, empresa_id: int, competencia: str) -> Tuple[float, float]:
        comp_ant = competencia_anterior(competencia)
        fechamento_ant = self.buscar_fechamento(empresa_id, comp_ant)
        if not fechamento_ant:
            return 0.0, 0.0

        return (
            round(fechamento_ant["saldo_transportar_pis"] or 0, 2),
            round(fechamento_ant["saldo_transportar_cofins"] or 0, 2),
        )

    def fechar_competencia(self, empresa_id: int, competencia: str, usuario_id: int, observacao: str = "") -> int:
        competencia = normalizar_competencia(competencia)

        cur = self.db.execute("""
            SELECT
                SUM(CASE WHEN natureza = 'debito' THEN pis_individual ELSE 0 END) AS total_debito_pis,
                SUM(CASE WHEN natureza = 'credito' THEN pis_individual ELSE 0 END) AS total_credito_pis,
                SUM(CASE WHEN natureza = 'debito' THEN cofins_individual ELSE 0 END) AS total_debito_cofins,
                SUM(CASE WHEN natureza = 'credito' THEN cofins_individual ELSE 0 END) AS total_credito_cofins
            FROM lancamentos
            WHERE empresa_id = ? AND competencia = ?
        """, (empresa_id, competencia))
        totais = cur.fetchone()

        cur = self.db.execute("""
            SELECT
                SUM(CASE WHEN status = 'disponivel' THEN pis_individual ELSE 0 END) AS cred_retro_pis,
                SUM(CASE WHEN status = 'disponivel' THEN cofins_individual ELSE 0 END) AS cred_retro_cofins
            FROM lancamentos
            WHERE empresa_id = ?
              AND origem_retroativa = 1
              AND competencia_aproveitamento = ?
              AND (aproveitado_em_competencia IS NULL OR aproveitado_em_competencia = '')
        """, (empresa_id, competencia))
        retro = cur.fetchone()

        saldo_ant_pis, saldo_ant_cofins = self.saldo_transportado_anterior(empresa_id, competencia)

        total_debito_pis = round((totais["total_debito_pis"] or 0), 2)
        total_credito_pis = round((totais["total_credito_pis"] or 0), 2)
        total_debito_cofins = round((totais["total_debito_cofins"] or 0), 2)
        total_credito_cofins = round((totais["total_credito_cofins"] or 0), 2)

        credito_retro_pis = round((retro["cred_retro_pis"] or 0), 2)
        credito_retro_cofins = round((retro["cred_retro_cofins"] or 0), 2)

        resultado_pis = round(total_debito_pis - total_credito_pis - saldo_ant_pis - credito_retro_pis, 2)
        resultado_cofins = round(total_debito_cofins - total_credito_cofins - saldo_ant_cofins - credito_retro_cofins, 2)

        saldo_transportar_pis = abs(resultado_pis) if resultado_pis < 0 else 0.0
        saldo_transportar_cofins = abs(resultado_cofins) if resultado_cofins < 0 else 0.0

        existente = self.buscar_fechamento(empresa_id, competencia)
        antes = to_dict(existente)

        if existente:
            self.db.execute("""
                UPDATE fechamentos
                SET total_debito_pis = ?,
                    total_credito_pis = ?,
                    saldo_anterior_pis = ?,
                    credito_retroativo_pis = ?,
                    resultado_pis = ?,
                    saldo_transportar_pis = ?,
                    total_debito_cofins = ?,
                    total_credito_cofins = ?,
                    saldo_anterior_cofins = ?,
                    credito_retroativo_cofins = ?,
                    resultado_cofins = ?,
                    saldo_transportar_cofins = ?,
                    status = 'fechado',
                    observacao = ?,
                    fechado_por = ?,
                    fechado_em = ?,
                    updated_at = ?
                WHERE empresa_id = ? AND competencia = ?
            """, (
                total_debito_pis,
                total_credito_pis,
                saldo_ant_pis,
                credito_retro_pis,
                resultado_pis,
                saldo_transportar_pis,
                total_debito_cofins,
                total_credito_cofins,
                saldo_ant_cofins,
                credito_retro_cofins,
                resultado_cofins,
                saldo_transportar_cofins,
                observacao,
                usuario_id,
                agora_str(),
                agora_str(),
                empresa_id,
                competencia
            ))
            fechamento_id = existente["id"]
        else:
            cur2 = self.db.execute("""
                INSERT INTO fechamentos (
                    empresa_id, competencia,
                    total_debito_pis, total_credito_pis, saldo_anterior_pis, credito_retroativo_pis, resultado_pis, saldo_transportar_pis,
                    total_debito_cofins, total_credito_cofins, saldo_anterior_cofins, credito_retroativo_cofins, resultado_cofins, saldo_transportar_cofins,
                    status, observacao, fechado_por, fechado_em, created_at, updated_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'fechado', ?, ?, ?, ?, ?)
            """, (
                empresa_id,
                competencia,
                total_debito_pis,
                total_credito_pis,
                saldo_ant_pis,
                credito_retro_pis,
                resultado_pis,
                saldo_transportar_pis,
                total_debito_cofins,
                total_credito_cofins,
                saldo_ant_cofins,
                credito_retro_cofins,
                resultado_cofins,
                saldo_transportar_cofins,
                observacao,
                usuario_id,
                agora_str(),
                agora_str(),
                agora_str()
            ))
            fechamento_id = cur2.lastrowid

        self.db.execute("""
            UPDATE lancamentos
            SET status = 'aproveitado',
                aproveitado_em_competencia = ?,
                updated_at = ?,
                updated_by = ?
            WHERE empresa_id = ?
              AND origem_retroativa = 1
              AND competencia_aproveitamento = ?
              AND (aproveitado_em_competencia IS NULL OR aproveitado_em_competencia = '')
        """, (
            competencia,
            agora_str(),
            usuario_id,
            empresa_id,
            competencia
        ))

        self.db.commit()

        depois = to_dict(self.buscar_fechamento(empresa_id, competencia))
        self.auditoria.registrar(
            usuario_id=usuario_id,
            acao="FECHAR_COMPETENCIA",
            entidade="fechamentos",
            registro_id=fechamento_id,
            competencia=competencia,
            motivo=observacao,
            antes=antes,
            depois=depois
        )

        return fechamento_id

    def reabrir_competencia(self, empresa_id: int, competencia: str, usuario_id: int, motivo: str):
        competencia = normalizar_competencia(competencia)

        if not motivo.strip():
            raise ValueError("Motivo é obrigatório para reabrir competência.")

        fechamento = self.buscar_fechamento(empresa_id, competencia)
        if not fechamento:
            raise ValueError("Competência não possui fechamento.")

        antes = to_dict(fechamento)

        self.db.execute("""
            UPDATE fechamentos
            SET status = 'reaberto',
                reaberto_por = ?,
                reaberto_em = ?,
                updated_at = ?
            WHERE empresa_id = ? AND competencia = ?
        """, (
            usuario_id,
            agora_str(),
            agora_str(),
            empresa_id,
            competencia
        ))
        self.db.commit()

        depois = to_dict(self.buscar_fechamento(empresa_id, competencia))
        self.auditoria.registrar(
            usuario_id=usuario_id,
            acao="REABRIR_COMPETENCIA",
            entidade="fechamentos",
            registro_id=fechamento["id"],
            competencia=competencia,
            motivo=motivo,
            antes=antes,
            depois=depois
        )


# =========================================================
# LANÇAMENTOS
# =========================================================

class LancamentoService:
    def __init__(self, db: Database, auditoria: AuditoriaService, fechamento_service: FechamentoService):
        self.db = db
        self.auditoria = auditoria
        self.fechamento_service = fechamento_service

    def buscar_lancamento(self, lancamento_id: int) -> Optional[sqlite3.Row]:
        cur = self.db.execute("SELECT * FROM lancamentos WHERE id = ?", (lancamento_id,))
        return cur.fetchone()

    def criar_lancamento(
        self,
        empresa_id: int,
        operacao_id: int,
        competencia: str,
        valor_base: float,
        observacao: str,
        usuario_id: int,
        origem_retroativa: int = 0,
        competencia_origem: Optional[str] = None,
        data_apresentacao: Optional[str] = None,
        competencia_aproveitamento: Optional[str] = None,
        motivo_edicao_mes_fechado: str = ""
    ) -> int:
        competencia = normalizar_competencia(competencia)

        if valor_base <= 0:
            raise ValueError("valor_base deve ser maior que zero.")

        if self.fechamento_service.competencia_fechada(empresa_id, competencia):
            if not motivo_edicao_mes_fechado.strip():
                raise ValueError("Alteração em competência fechada exige motivo obrigatório.")

        operacao = self.db.execute("SELECT * FROM operacoes WHERE id = ?", (operacao_id,)).fetchone()
        if not operacao:
            raise ValueError("Operação não encontrada.")

        natureza = operacao["natureza"]
        receita_financeira = bool(operacao["receita_financeira"])

        pis, cofins = RegraFiscalService.calcular_pis_cofins(valor_base, receita_financeira)

        status = "normal"
        comp_aprov = competencia_aproveitamento
        comp_origem = competencia_origem

        if origem_retroativa:
            status = "disponivel"

            if not comp_origem:
                raise ValueError("Crédito retroativo exige competencia_origem.")
            if not data_apresentacao:
                raise ValueError("Crédito retroativo exige data_apresentacao.")

            comp_origem = normalizar_competencia(comp_origem)

            if not comp_aprov:
                comp_aprov = RegraFiscalService.determinar_competencia_aproveitamento(data_apresentacao)

        cur = self.db.execute("""
            INSERT INTO lancamentos (
                empresa_id, operacao_id, competencia, valor_base, natureza,
                pis_individual, cofins_individual, observacao,
                origem_retroativa, competencia_origem, data_apresentacao,
                competencia_aproveitamento, aproveitado_em_competencia, status,
                created_by, created_at, updated_by, updated_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            empresa_id,
            operacao_id,
            competencia,
            round(valor_base, 2),
            natureza,
            pis,
            cofins,
            observacao,
            origem_retroativa,
            comp_origem,
            data_apresentacao,
            comp_aprov,
            None,
            status,
            usuario_id,
            agora_str(),
            usuario_id,
            agora_str()
        ))
        self.db.commit()

        lancamento_id = cur.lastrowid
        depois = to_dict(self.buscar_lancamento(lancamento_id))

        self.auditoria.registrar(
            usuario_id=usuario_id,
            acao="CRIAR_LANCAMENTO",
            entidade="lancamentos",
            registro_id=lancamento_id,
            competencia=competencia,
            motivo=motivo_edicao_mes_fechado if motivo_edicao_mes_fechado else observacao,
            antes=None,
            depois=depois
        )

        return lancamento_id

    def listar_lancamentos_por_competencia(self, empresa_id: int, competencia: str) -> List[sqlite3.Row]:
        competencia = normalizar_competencia(competencia)
        cur = self.db.execute("""
            SELECT l.*, o.nome AS operacao_nome
            FROM lancamentos l
            JOIN operacoes o ON o.id = l.operacao_id
            WHERE l.empresa_id = ? AND l.competencia = ?
            ORDER BY l.id
        """, (empresa_id, competencia))
        return cur.fetchall()


# =========================================================
# CUSTOS
# =========================================================

class CustoService:
    def __init__(self, db: Database, auditoria: AuditoriaService):
        self.db = db
        self.auditoria = auditoria

    def tipo_custo_por_tipo_empresa(self, tipo_empresa: str) -> str:
        mapa = {
            "industria": "CPV",
            "comercio": "CMV",
            "servico": "CSV",
        }
        if tipo_empresa not in mapa:
            raise ValueError("tipo_empresa inválido.")
        return mapa[tipo_empresa]

    def calcular_e_registrar_custo(
        self,
        empresa_id: int,
        competencia: str,
        valor_bruto: float,
        observacao: str,
        usuario_id: int
    ) -> int:
        if valor_bruto <= 0:
            raise ValueError("valor_bruto deve ser maior que zero.")

        empresa = self.db.execute("SELECT * FROM empresas WHERE id = ?", (empresa_id,)).fetchone()
        if not empresa:
            raise ValueError("Empresa não encontrada.")

        tipo_custo = self.tipo_custo_por_tipo_empresa(empresa["tipo_empresa"])
        pis_custo, cofins_custo = RegraFiscalService.calcular_pis_cofins(valor_bruto, receita_financeira=False)
        valor_liquido = round(valor_bruto - pis_custo - cofins_custo, 2)

        cur = self.db.execute("""
            INSERT INTO custos (
                empresa_id, competencia, tipo_custo, valor_bruto,
                pis_custo, cofins_custo, valor_liquido,
                observacao, created_by, created_at, updated_by, updated_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            empresa_id,
            normalizar_competencia(competencia),
            tipo_custo,
            round(valor_bruto, 2),
            pis_custo,
            cofins_custo,
            valor_liquido,
            observacao,
            usuario_id,
            agora_str(),
            usuario_id,
            agora_str()
        ))
        self.db.commit()

        custo_id = cur.lastrowid
        custo = self.db.execute("SELECT * FROM custos WHERE id = ?", (custo_id,)).fetchone()

        self.auditoria.registrar(
            usuario_id=usuario_id,
            acao="CRIAR_CUSTO",
            entidade="custos",
            registro_id=custo_id,
            competencia=competencia,
            motivo=observacao,
            antes=None,
            depois=to_dict(custo)
        )

        return custo_id


# =========================================================
# RELATÓRIO SIMPLES DE CONSOLE
# =========================================================

class RelatorioService:
    def __init__(self, db: Database):
        self.db = db

    def resumo_competencia(self, empresa_id: int, competencia: str) -> Dict[str, Any]:
        competencia = normalizar_competencia(competencia)

        cur = self.db.execute("""
            SELECT
                SUM(CASE WHEN natureza='debito' THEN valor_base ELSE 0 END) AS base_debito,
                SUM(CASE WHEN natureza='credito' THEN valor_base ELSE 0 END) AS base_credito,
                SUM(CASE WHEN natureza='debito' THEN pis_individual ELSE 0 END) AS pis_debito,
                SUM(CASE WHEN natureza='credito' THEN pis_individual ELSE 0 END) AS pis_credito,
                SUM(CASE WHEN natureza='debito' THEN cofins_individual ELSE 0 END) AS cofins_debito,
                SUM(CASE WHEN natureza='credito' THEN cofins_individual ELSE 0 END) AS cofins_credito
            FROM lancamentos
            WHERE empresa_id = ? AND competencia = ?
        """, (empresa_id, competencia))
        row = cur.fetchone()

        fechamento = self.db.execute("""
            SELECT * FROM fechamentos
            WHERE empresa_id = ? AND competencia = ?
        """, (empresa_id, competencia)).fetchone()

        return {
            "competencia": competencia,
            "base_debito": round((row["base_debito"] or 0), 2),
            "base_credito": round((row["base_credito"] or 0), 2),
            "pis_debito": round((row["pis_debito"] or 0), 2),
            "pis_credito": round((row["pis_credito"] or 0), 2),
            "cofins_debito": round((row["cofins_debito"] or 0), 2),
            "cofins_credito": round((row["cofins_credito"] or 0), 2),
            "fechamento": to_dict(fechamento)
        }


# =========================================================
# FUNÇÕES DE EXEMPLO
# =========================================================

def inicializar_banco():
    db = Database()
    Migrator(db).migrate()
    AuthService(db).garantir_admin_padrao()
    db.close()
    print("Banco inicializado com sucesso.")


def cadastrar_dados_exemplo():
    db = Database()

    empresa_service = EmpresaService(db)
    operacao_service = OperacaoService(db)
    erp_service = ERPService(db)

    cur = db.execute("SELECT COUNT(*) AS total FROM empresas")
    if cur.fetchone()["total"] == 0:
        empresa_id = empresa_service.criar_empresa(
            nome="Minha Empresa Contábil LTDA",
            cnpj="00.000.000/0001-00",
            regime_tributario="Lucro Real",
            tipo_empresa="servico",
            tipo_estabelecimento="matriz",
            grupo_empresarial="Grupo Exemplo",
            cnae="23.30-3-05",
            descricao_cnae="Preparação de massa de concreto e argamassa para construção"
        )
        print(f"Empresa exemplo criada. ID={empresa_id}")

    cur = db.execute("SELECT COUNT(*) AS total FROM operacoes")
    if cur.fetchone()["total"] == 0:
        op1 = operacao_service.criar_operacao("Venda de Serviços", "debito", "receita_operacional", 0)
        op2 = operacao_service.criar_operacao("Receita Financeira", "debito", "receita_financeira", 1)
        op3 = operacao_service.criar_operacao("Compra Mercador/Insumos", "credito", "credito_insumos", 0)
        print(f"Operações exemplo criadas: {op1}, {op2}, {op3}")

    cur = db.execute("SELECT COUNT(*) AS total FROM mapeamentos_erp")
    if cur.fetchone()["total"] == 0:
        erp_service.criar_mapeamento("PIS A RECUPERAR", "1.1.01", "2.1.01", "Vr. ref. Pis a recuperar {competencia}")
        erp_service.criar_mapeamento("COFINS A RECUPERAR", "1.1.02", "2.1.02", "Vr. ref. Cofins a recuperar {competencia}")
        erp_service.criar_mapeamento("PIS DEBITO", "3.1.01", "2.1.03", "Vr. ref. Pis {competencia}")
        erp_service.criar_mapeamento("COFINS DEBITO", "3.1.02", "2.1.04", "Vr. ref. Cofins {competencia}")
        print("Mapeamentos ERP exemplo criados.")

    db.close()


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

    print("\n--- Lançando débito normal ---")
    lanc1 = lanc_service.criar_lancamento(
        empresa_id=empresa_id,
        operacao_id=mapa_ops["Venda de Serviços"],
        competencia="03/2026",
        valor_base=150000.00,
        observacao="Venda de serviços março",
        usuario_id=usuario_id
    )
    print(f"Lançamento criado: {lanc1}")

    print("\n--- Lançando receita financeira ---")
    lanc2 = lanc_service.criar_lancamento(
        empresa_id=empresa_id,
        operacao_id=mapa_ops["Receita Financeira"],
        competencia="03/2026",
        valor_base=10000.00,
        observacao="Receita financeira março",
        usuario_id=usuario_id
    )
    print(f"Lançamento criado: {lanc2}")

    print("\n--- Lançando crédito normal ---")
    lanc3 = lanc_service.criar_lancamento(
        empresa_id=empresa_id,
        operacao_id=mapa_ops["Compra Mercador/Insumos"],
        competencia="03/2026",
        valor_base=40000.00,
        observacao="Compra de insumos março",
        usuario_id=usuario_id
    )
    print(f"Lançamento criado: {lanc3}")

    print("\n--- Lançando crédito retroativo de janeiro apresentado em março ---")
    lanc4 = lanc_service.criar_lancamento(
        empresa_id=empresa_id,
        operacao_id=mapa_ops["Compra Mercador/Insumos"],
        competencia="01/2026",
        valor_base=12000.00,
        observacao="NF janeiro apresentada em março",
        usuario_id=usuario_id,
        origem_retroativa=1,
        competencia_origem="01/2026",
        data_apresentacao="2026-03-20"
    )
    print(f"Lançamento retroativo criado: {lanc4}")

    print("\n--- Calculando custo ---")
    custo_id = custo_service.calcular_e_registrar_custo(
        empresa_id=empresa_id,
        competencia="03/2026",
        valor_bruto=50000.00,
        observacao="Cálculo de custo do mês",
        usuario_id=usuario_id
    )
    print(f"Custo registrado: {custo_id}")

    print("\n--- Fechando competência 03/2026 ---")
    fechamento_id = fechamento_service.fechar_competencia(
        empresa_id=empresa_id,
        competencia="03/2026",
        usuario_id=usuario_id,
        observacao="Fechamento inicial março/2026"
    )
    print(f"Fechamento realizado: {fechamento_id}")

    print("\n--- Resumo da competência ---")
    resumo = relatorio_service.resumo_competencia(empresa_id, "03/2026")
    print(json.dumps(resumo, indent=2, ensure_ascii=False, default=str))

    db.close()


if __name__ == "__main__":
    inicializar_banco()
    cadastrar_dados_exemplo()
    testar_fluxo_basico()
