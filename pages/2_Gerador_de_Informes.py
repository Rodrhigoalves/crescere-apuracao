import streamlit as st
import pandas as pd
from docxtpl import DocxTemplate
import io
import zipfile
import os

# Configuração para o monitor do ASUS TUF
st.set_page_config(page_title="Gerador de Informes Pro", layout="wide")

st.title("📄 Gerador de Informe de Rendimentos")
st.markdown("---")

# --- FUNÇÕES DE FORMATAÇÃO E LIMPEZA ---

def formatar_moeda(valor):
    """Formata para 6.500,00 mantendo a string curta para não quebrar o layout"""
    try:
        if pd.isna(valor) or valor == "" or valor == 0:
            return "0,00"
        return f"{float(valor):,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")
    except:
        return "0,00"

def formatar_documento(doc):
    """Aplica máscara de CPF/CNPJ e limpa espaços"""
    doc = str(doc).translate(str.maketrans("", "", "./- ")).strip()
    if len(doc) == 11:
        return f"{doc[:3]}.{doc[3:6]}.{doc[6:9]}-{doc[9:]}"
    elif len(doc) == 14:
        return f"{doc[:2]}.{doc[2:5]}.{doc[5:8]}/{doc[8:12]}-{doc[12:]}"
    return doc

def localizar_template():
    """Busca automática do Word na raiz do projeto"""
    current_dir = os.path.dirname(os.path.abspath(__file__))
    raiz = os.path.normpath(os.path.join(current_dir, ".."))
    try:
        arquivos = os.listdir(raiz)
        for arq in arquivos:
            if arq.lower().endswith(".docx"):
                return os.path.join(raiz, arq)
    except:
        return None
    return None

# --- EXECUÇÃO ---

template_path = localizar_template()

if template_path:
    st.success(f"✅ Template detetado: **{os.path.basename(template_path)}**")
else:
    st.error("❌ Erro: Arquivo .docx não encontrado na raiz do GitHub.")
    st.stop()

uploaded_file = st.file_uploader("Suba a planilha Excel", type=["xlsx"])

if uploaded_file:
    try:
        df = pd.read_excel(uploaded_file)
        st.dataframe(df.head())

        if st.button("🚀 Gerar Informes em Lote"):
            zip_buffer = io.BytesIO()
            
            with zipfile.ZipFile(zip_buffer, "a", zipfile.ZIP_DEFLATED) as zip_file:
                barra = st.progress(0)
                
                for index, row in df.iterrows():
                    doc_tpl = DocxTemplate(template_path)
                    
                    # --- LIMPEZA DE DADOS (PREVINE CORTE DE TEXTO) ---
                    # Criamos um contexto limpando espaços em branco que deformam tabelas
                    context = {}
                    for k, v in row.to_dict().items():
                        if pd.isna(v):
                            context[k] = ""
                        elif isinstance(v, str):
                            context[k] = v.strip() # Remove espaços fantasmas
                        else:
                            context[k] = v

                    # Aplica formatações específicas
                    context['valor_aluguel'] = formatar_moeda(context.get('valor_aluguel'))
                    context['ir_retido'] = formatar_moeda(context.get('ir_retido'))
                    
                    if 'cnpj_fonte' in context:
                        context['cnpj_fonte'] = formatar_documento(context['cnpj_fonte'])
                    if 'cpf_beneficiario' in context:
                        context['cpf_beneficiario'] = formatar_documento(context['cpf_beneficiario'])
                    
                    context['data_emissao'] = "31/12/2025"

                    # Renderização
                    doc_tpl.render(context)
                    
                    doc_io = io.BytesIO()
                    doc_tpl.save(doc_io)
                    
                    nome_limpo = str(row.get('nome_beneficiario', index)).strip().replace(" ", "_")
                    zip_file.writestr(f"Informe_{nome_limpo}.docx", doc_io.getvalue())
                    
                    barra.progress((index + 1) / len(df))

            st.success("✅ Documentos gerados!")
            st.download_button(
                label="📥 Baixar ZIP",
                data=zip_buffer.getvalue(),
                file_name="Informes_Rendimentos.zip",
                mime="application/zip"
            )
                
    except Exception as e:
        st.error(f"Erro técnico: {e}")
