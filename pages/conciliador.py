import streamlit as st
import pandas as pd
import pdfplumber
import mysql.connector
import io
import re
import unicodedata
from thefuzz import fuzz
from ofxparse import OfxParser

# ---------------------------------------------------------
# 1. UTILITÁRIOS E CONEXÃO
# ---------------------------------------------------------
def get_connection():
    return mysql.connector.connect(
        host=st.secrets["mysql"]["host"],
        user=st.secrets["mysql"]["user"],
        password=st.secrets["mysql"]["password"],
        database=st.secrets["mysql"]["database"],
        use_pure=True,      
        ssl_disabled=True   
    )

def padronizar_texto(texto):
    if not texto: return ""
    texto_sem_acento = unicodedata.normalize('NFKD', str(texto)).encode('ASCII', 'ignore').decode('utf-8')
    texto_limpo = re.sub(r'\s+', ' ', texto_sem_acento.upper().strip())
    return texto_limpo

def formatar_moeda(valor):
    """Formata float para R$ no padrão brasileiro"""
    return f"R$ {valor:,.2f}".replace(',', 'X').replace('.', ',').replace('X', '.')

# ---------------------------------------------------------
# 2. MOTOR DE EXTRAÇÃO PDF
# ---------------------------------------------------------
def _extrair_nome_final(chunk: str) -> str:
    texto = re.sub(r'\d{1,3}(?:\.\d{3})*,\d{2}', '', chunk)
    texto = re.sub(r'R\$|\bS\.A\b\.?', '', texto)
    texto = re.sub(r'[|\-]', ' ', texto)
    texto = re.sub(r'\s+', ' ', texto).strip()

    RUIDO_TOKENS = {
        'parcela', 'emprestimo', 'transferencia', 'pix', 'maquininha',
        'debito', 'credito', 'tarifa', 'maestro', 'elo', 'visa',
        'mastercard', 'stone', 'instituicao', 'pagamento', 'sa',
        'saque', 'deposito', 'ted', 'doc', 'boleto', 'cartao'
    }

    tokens = texto.strip().split()
    nome_tokens = []
    for tok in reversed(tokens):
        tok_norm = unicodedata.normalize('NFKD', tok).encode('ASCII', 'ignore').decode().lower()
        if len(tok_norm) < 2 or tok_norm in RUIDO_TOKENS:
            if nome_tokens:  
                break
            continue        
        if re.match(r'^[A-Za-záéíóúâêîôûãõçàÁÉÍÓÚÂÊÎÔÛÃÕÇÀ]{2,}$', tok):
            nome_tokens.insert(0, tok)
        else:
            if nome_tokens:
                break

    return " ".join(nome_tokens)

@st.cache_data(show_spinner=False)
def extrair_por_recintos(file_bytes):
    texto_completo = ""
    with pdfplumber.open(io.BytesIO(file_bytes)) as pdf:
        for page in pdf.pages:
            palavras = page.extract_words(x_tolerance=3, y_tolerance=3)
            linhas_dict = {}
            
            for p in palavras:
                y = round(float(p['top']))
                encontrou_y = None
                for key in linhas_dict.keys():
                    if abs(key - y) <= 3:
                        encontrou_y = key
                        break
                
                if encontrou_y is not None:
                    linhas_dict[encontrou_y].append(p)
                else:
                    linhas_dict[y] = [p]

            for y_key in sorted(linhas_dict.keys()):
                linha_ordenada = sorted(linhas_dict[y_key], key=lambda x: x['x0'])
                texto_linha = " ".join([w['text'] for w in linha_ordenada])
                texto_completo += texto_linha + "\n"

    RUIDO = ["período:", "página", "saldo anterior", "saldo atual", "saldo final",
             "data tipo descri", "cnpj", "emitido em", "extrato de conta",
             "dados da conta", "nome documento", "instituição agência",
             "contraparte stone"]
    linhas = [l.strip() for l in texto_completo.split('\n')
              if l.strip() and not any(x in l.lower() for x in RUIDO)]
    texto = " ".join(linhas)

    ANCHOR = r'(\d{2}/\d{2}/\d{2,4})\s+(Saída|Entrada|Saque|Depósito)'
    partes = re.split(ANCHOR, texto)

    n = (len(partes) - 1) // 3
    dados = []

    for i in range(n):
        data  = partes[i * 3 + 1]
        tipo  = partes[i * 3 + 2]
        corpo = partes[i * 3 + 3].strip()

        chunk_anterior = partes[(i - 1) * 3 + 3] if i > 0 else partes[0]
        contraparte = _extrair_nome_final(chunk_anterior)

        valores = re.findall(r'\d{1,3}(?:\.\d{3})*,\d{2}', corpo)
        if not valores:
            continue
        valor_num = float(valores[0].replace('.', '').replace(',', '.'))
        sinal = '+' if tipo.lower() == 'entrada' else '-'

        sub = re.search(r'([A-ZÀ-Úa-zà-ú]+(?:\s+[A-ZÀ-Úa-zà-ú]+)*\s*\|\s*[A-ZÀ-Úa-zà-ú]+(?:\s+[A-ZÀ-Úa-zà-ú]+)*|Tarifa)', corpo)
        subcategoria = sub.group(1).strip() if sub else ""

        desc_parts = [p for p in [contraparte, subcategoria] if p]
        desc = " ".join(desc_parts) if desc_parts else corpo[:60]

        dados.append({
            'Data':      data,
            'Descricao': padronizar_texto(desc),
            'Valor':     abs(valor_num),
            'Sinal':     sinal
        })

    return pd.DataFrame(dados)

@st.cache_data(show_spinner=False)
def extrair_texto_ofx(file_bytes):
    dados_extraidos = []
    ofx = OfxParser.parse(io.BytesIO(file_bytes))
    for account in ofx.accounts:
        for tx in account.statement.transactions:
            valor = float(tx.amount)
            dados_extraidos.append({
                'Data': tx.date.strftime('%d/%m/%Y'),
                'Descricao': padronizar_texto(tx.payee if tx.payee else tx.memo),
                'Valor': abs(valor),
                'Sinal': '+' if valor > 0 else '-'
            })
    return pd.DataFrame(dados_extraidos)

# ---------------------------------------------------------
# 3. INTERFACE PRINCIPAL
# ---------------------------------------------------------
st.set_page_config(page_title="Conciliador Pro", page_icon="🎯", layout="wide")

if 'skipped_indices' not in st.session_state:
    st.session_state.skipped_indices = []
if 'editando_regra_id' not in st.session_state:
    st.session_state.editando_regra_id = None

st.title("🎯 Conciliador Pro - Mesa por Cliques")

conn = get_connection()
empresas = pd.read_sql("SELECT id, nome FROM empresas", conn)

# --- CONFIGURAÇÕES INICIAIS ---
col_cfg1, col_cfg2, col_cfg3 = st.columns([2, 1, 1])
empresa_sel = col_cfg1.selectbox("Empresa / Filial", empresas['nome'])
id_empresa = int(empresas[empresas['nome'] == empresa_sel]['id'].values[0])
conta_banco_fixa = col_cfg2.text_input("Conta Banco (Âncora)", value="196")
# NOVO: Campo para o usuário informar o saldo inicial
saldo_anterior_informado = col_cfg3.number_input("Saldo Anterior (R$)", value=0.00, step=100.00, format="%.2f")

uploaded_files = st.file_uploader(
    "Arraste seus extratos (PDF ou OFX)", type=["pdf", "ofx"], accept_multiple_files=True
)

if uploaded_files and conta_banco_fixa:
    with st.spinner("Processando extratos..."):
        lista_dfs = []
        for file in uploaded_files:
            file_name = file.name.lower()
            if file_name.endswith('.pdf'):
                lista_dfs.append(extrair_por_recintos(file.getvalue()))
            elif file_name.endswith('.ofx'):
                lista_dfs.append(extrair_texto_ofx(file.getvalue()))

        df_bruto = pd.concat(lista_dfs, ignore_index=True)
        regras = pd.read_sql(
            f"SELECT * FROM tb_extratos_regras WHERE id_empresa = {id_empresa}", conn
        )

    # ---------------------------------------------------------
    # NOVO: AUDITORIA DE SALDOS
    # ---------------------------------------------------------
    st.divider()
    st.subheader("📊 Auditoria de Leitura (Prova de Caixa)")
    
    total_entradas = df_bruto[df_bruto['Sinal'] == '+']['Valor'].sum()
    total_saidas = df_bruto[df_bruto['Sinal'] == '-']['Valor'].sum()
    saldo_final_calculado = saldo_anterior_informado + total_entradas - total_saidas

    col_aud1, col_aud2, col_aud3, col_aud4 = st.columns(4)
    col_aud1.metric("Saldo Anterior", formatar_moeda(saldo_anterior_informado))
    col_aud2.metric("🟢 Total Lido (Entradas)", formatar_moeda(total_entradas))
    col_aud3.metric("🔴 Total Lido (Saídas)", formatar_moeda(total_saidas))
    col_aud4.metric("⚖️ Saldo Final Calculado", formatar_moeda(saldo_final_calculado))

    st.caption("💡 *Dica: Compare o **Saldo Final Calculado** com o saldo impresso no final do seu extrato PDF. Se forem iguais, 100% das linhas foram lidas com sucesso!*")
    st.divider()

    # --- DEBUG ---
    with st.expander("🔍 Ver dados brutos extraídos"):
        st.dataframe(df_bruto, use_container_width=True)

    # --- CLASSIFICAÇÃO ---
    prontos, pendentes = [], []
    for idx, row in df_bruto.iterrows():
        match = False
        for _, r in regras.iterrows():
            if (fuzz.partial_ratio(padronizar_texto(r['termo_chave']), row['Descricao']) >= 85
                    and r['sinal_esperado'] == row['Sinal']):
                if r['conta_contabil'] != 'IGNORAR':
                    d = conta_banco_fixa if row['Sinal'] == '+' else r['conta_contabil']
                    c = r['conta_contabil'] if row['Sinal'] == '+' else conta_banco_fixa
                    prontos.append({
                        'Debito': d, 'Credito': c, 'Data': row['Data'],
                        'Valor': f"{row['Valor']:.2f}".replace('.', ','),
                        'Historico': r['historico_padrao'] if r['historico_padrao'] else row['Descricao']
                    })
                match = True
                break
        if not match:
            pendentes.append({'idx_original': idx, **row})

    # ---------------------------------------------------------
    # FILA DE PENDENTES
    # ---------------------------------------------------------
    df_p = pd.DataFrame(pendentes)
    if not df_p.empty:
        fila_atual = df_p[~df_p['idx_original'].isin(st.session_state.skipped_indices)]

        if fila_atual.empty and st.session_state.skipped_indices:
            st.session_state.skipped_indices = []
            st.rerun()

        st.metric("Lançamentos Pendentes", len(df_p))

        if not fila_atual.empty:
            item = fila_atual.iloc[0]
            st.subheader("🎓 Mesa de Treinamento por Cliques")

            col_i1, col_i2, col_i3, col_i4 = st.columns(4)
            col_i1.metric("📅 Data", item['Data'])
            col_i2.metric("💰 Valor", formatar_moeda(item['Valor']))
            col_i3.metric("↕️ Tipo", "🟢 Entrada" if item['Sinal'] == '+' else "🔴 Saída")
            col_i4.metric("📝 Descrição completa", item['Descricao'][:40] + ("..." if len(item['Descricao']) > 40 else ""))

            palavras = item['Descricao'].split()
            st.write("**Selecione as palavras que definem a regra:**")
            selecionadas = st.pills(
                "Palavras", palavras, selection_mode="multi", label_visibility="collapsed"
            )
            termo_final = " ".join(selecionadas) if selecionadas else ""
            st.text_input("Sua Regra será:", value=termo_final, disabled=True)

            if termo_final:
                df_impactados = df_p[df_p['Descricao'].str.contains(re.escape(termo_final), na=False)]
                impacto = len(df_impactados)
                st.info(f"💡 Esta regra limpa **{impacto}** lançamentos.")
                with st.expander(f"📋 Ver lançamentos impactados ({impacto})", expanded=False):
                    st.dataframe(
                        df_impactados[['Data', 'Descricao', 'Valor', 'Sinal']].reset_index(drop=True),
                        use_container_width=True
                    )

            with st.form("form_treino"):
                c1, c2, c3 = st.columns(3)
                contra = c1.text_input("Contrapartida")
                cod_h  = c2.text_input("Cód. Hist.")
                txt_h  = c3.text_input("Histórico Padrão")

                b1, b2, b3, b4 = st.columns(4)
                if b1.form_submit_button("✅ Salvar Regra", use_container_width=True):
                    if termo_final and contra:
                        cursor = conn.cursor()
                        cursor.execute(
                            "INSERT INTO tb_extratos_regras (id_empresa, banco_nome, termo_chave, sinal_esperado, conta_contabil, cod_historico_erp, historico_padrao) VALUES (%s, %s, %s, %s, %s, %s, %s)",
                            (id_empresa, 'PADRAO', termo_final, item['Sinal'], contra, cod_h, txt_h)
                        )
                        conn.commit()
                        st.rerun()

                if b2.form_submit_button("🗑️ Ignorar Lixo", use_container_width=True):
                    if termo_final:
                        cursor = conn.cursor()
                        cursor.execute(
                            "INSERT INTO tb_extratos_regras (id_empresa, banco_nome, termo_chave, sinal_esperado, conta_contabil) VALUES (%s, %s, %s, %s, %s)",
                            (id_empresa, 'PADRAO', termo_final, item['Sinal'], 'IGNORAR')
                        )
                        conn.commit()
                        st.rerun()

                if b3.form_submit_button("⏭️ Pular", use_container_width=True):
                    st.session_state.skipped_indices.append(item['idx_original'])
                    st.rerun()

                if b4.form_submit_button("🔄 Resetar Fila", use_container_width=True):
                    st.session_state.skipped_indices = []
                    st.rerun()

    if not prontos:
        st.info("Aguardando processamento...")
    elif not pendentes:
        st.success("🎉 Tudo mapeado! Exportação liberada.")
        st.download_button(
            "📥 BAIXAR CSV ALTERDATA",
            pd.DataFrame(prontos).to_csv(index=False, sep=';', encoding='latin1'),
            "importar.csv", "text/csv", type="primary"
        )

# ---------------------------------------------------------
# 4. PAINEL DE REGRAS CADASTRADAS
# ---------------------------------------------------------
st.divider()
st.subheader("📚 Regras Cadastradas no Banco de Dados")

regras_view = pd.read_sql(
    f"SELECT * FROM tb_extratos_regras WHERE id_empresa = {id_empresa} ORDER BY id DESC",
    conn
)

if regras_view.empty:
    st.info("Nenhuma regra cadastrada para esta empresa ainda.")
else:
    if st.session_state.editando_regra_id is not None:
        regra_edit = regras_view[regras_view['id'] == st.session_state.editando_regra_id]
        if not regra_edit.empty:
            regra_edit = regra_edit.iloc[0]
            with st.container(border=True):
                st.markdown(f"#### ✏️ Editando regra ID `{st.session_state.editando_regra_id}`")
                with st.form("form_edicao"):
                    ec1, ec2 = st.columns(2)
                    novo_termo = ec1.text_input("Termo Chave", value=regra_edit['termo_chave'])
                    nova_conta = ec2.text_input("Conta Contábil", value=regra_edit['conta_contabil'])
                    ec3, ec4, ec5 = st.columns(3)
                    novo_sinal = ec3.selectbox(
                        "Sinal", ['+', '-'],
                        index=0 if regra_edit['sinal_esperado'] == '+' else 1
                    )
                    novo_cod_h = ec4.text_input("Cód. Hist.", value=regra_edit['cod_historico_erp'] or "")
                    novo_hist  = ec5.text_input("Histórico Padrão", value=regra_edit['historico_padrao'] or "")

                    sb1, sb2 = st.columns(2)
                    if sb1.form_submit_button("💾 Salvar Edição", use_container_width=True):
                        cursor = conn.cursor()
                        cursor.execute(
                            "UPDATE tb_extratos_regras SET termo_chave=%s, conta_contabil=%s, sinal_esperado=%s, cod_historico_erp=%s, historico_padrao=%s WHERE id=%s",
                            (novo_termo, nova_conta, novo_sinal, novo_cod_h, novo_hist,
                             st.session_state.editando_regra_id)
                        )
                        conn.commit()
                        st.session_state.editando_regra_id = None
                        st.rerun()
                    if sb2.form_submit_button("❌ Cancelar", use_container_width=True):
                        st.session_state.editando_regra_id = None
                        st.rerun()

    header = st.columns([1, 3, 2, 1, 3, 1, 1])
    for col, label in zip(header, ["ID", "Termo Chave", "Conta Contábil", "Sinal", "Histórico", "", ""]):
        col.markdown(f"**{label}**")

    for _, r in regras_view.iterrows():
        cols = st.columns([1, 3, 2, 1, 3, 1, 1])
        cols[0].write(str(r['id']))
        cols[1].write(r['termo_chave'])
        cols[2].write(r['conta_contabil'])
        cols[3].write(r['sinal_esperado'])
        cols[4].write(r['historico_padrao'] or "—")

        if cols[5].button("✏️ Editar", key=f"edit_{r['id']}", use_container_width=True):
            st.session_state.editando_regra_id = int(r['id'])
            st.rerun()

        if cols[6].button("🗑️ Excluir", key=f"del_{r['id']}", use_container_width=True):
            cursor = conn.cursor()
            cursor.execute("DELETE FROM tb_extratos_regras WHERE id = %s", (int(r['id']),))
            conn.commit()
            st.rerun()

conn.close()
