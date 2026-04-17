import streamlit as st
import pandas as pd
from docxtpl import DocxTemplate
import io
import zipfile
import os

st.set_page_config(page_title="Gerador de Informes", layout="wide")

st.title("📄 Gerador de Informe de Rendimentos")

def buscar_word_na_raiz():
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

template_path = buscar_word_na_raiz()

if template_path:
    st.success(f"✅ Template detectado: **{os.path.basename(template_path)}**")
else:
    st.error("❌ Nenhum arquivo Word (.docx) encontrado.")
    st.stop()

uploaded_file = st.file_uploader("Suba sua planilha de Aluguéis", type=["xlsx"])

if uploaded_file:
    try:
        # Lendo o Excel e tratando valores vazios (NaN) imediatamente
        df = pd.read_excel(uploaded_file)
        
        st.subheader("Prévia dos Dados")
        st.dataframe(df.head())

        if st.button("🚀 Gerar Informes em ZIP"):
            zip_buffer = io.BytesIO()
            
            with zipfile.ZipFile(zip_buffer, "a", zipfile.ZIP_DEFLATED) as zip_file:
                for index, row in df.iterrows():
                    doc = DocxTemplate(template_path)
                    
                    # Criamos o dicionário de dados da linha
                    context = row.to_dict()
                    
                    # --- TRATAMENTO PROFISSIONAL DE VALORES (Resolve o NaN) ---
                    campos_monetarios = ['valor_aluguel', 'ir_retido']
                    
                    for campo in campos_monetarios:
                        valor = context.get(campo)
                        # Se for NaN (vazio no Excel) ou nulo, define como 0
                        if pd.isna(valor) or valor == "":
                            valor = 0
                        
                        # Formata como moeda brasileira (ex: 1.500,00)
                        context[campo] = f"{float(valor):,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")
                    
                    # Garante que outros campos vazios não apareçam como 'nan' no texto
                    for chave, v in context.items():
                        if pd.isna(v):
                            context[chave] = ""

                    context['data_emissao'] = "31/12/2025"
                    
                    # Renderiza o documento preservando a formatação original
                    doc.render(context)
                    
                    doc_io = io.BytesIO()
                    doc.save(doc_io)
                    
                    nome_benef = str(row['nome_beneficiario']).strip().replace(" ", "_")
                    zip_file.writestr(f"Informe_{nome_benef}.docx", doc_io.getvalue())
            
            st.success("Processamento concluído com valores corrigidos!")
            st.download_button(
                label="📥 Baixar ZIP (Word)",
                data=zip_buffer.getvalue(),
                file_name="Informes_Corrigidos.zip",
                mime="application/zip"
            )
    except Exception as e:
        st.error(f"Erro no processamento: {e}")
