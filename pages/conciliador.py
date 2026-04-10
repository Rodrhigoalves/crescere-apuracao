# =============================================================================
# MOTORES CORRIGIDOS — substitua as funções equivalentes no seu app.py
# =============================================================================
#
# RESUMO DAS CORREÇÕES:
#
# extrair_pdf_itau — 4 problemas resolvidos:
#   1. in_movimento / current_date agora são PERSISTENTES entre páginas
#   2. current_date é carregado para todas as linhas do mesmo grupo de datas
#      (o Itaú repete a data apenas na 1ª linha de cada grupo)
#   3. Linhas com 2 valores: o último é o saldo corrente do dia → descartado
#   4. Sinal detectado pelo sufixo "-" (padrão do Itaú), sem precisar de
#      análise de posição de coluna.  Stop keywords impedem capturar o
#      bloco "Cheque Especial" e "Notas explicativas" como transações.
#
# extrair_planilha_bb — já estava correto; apenas documentado aqui.
#   - skiprows=2 pula as linhas 1-2 (título + agência) e começa no cabeçalho
#   - Colunas mapeadas: Data, Historico, Valor R$, Inf., Detalhamento Hist.
#   - Detalhamento é mesclado ao Historico removendo todos os números
#   - Sinal determinado pela coluna Inf. (C = crédito, D = débito)
#
# Stone / outros PDFs → extrair_por_recintos (não alterado, continua igual)
#
# =============================================================================

import io
import re
import unicodedata
import pandas as pd
import pdfplumber
import streamlit as st
import logging


def padronizar_texto(texto):
    if not texto:
        return ""
    texto_sem_acento = (
        unicodedata.normalize("NFKD", str(texto))
        .encode("ASCII", "ignore")
        .decode("utf-8")
    )
    return re.sub(r"\s+", " ", texto_sem_acento.upper().strip())


# ==========================================
# MOTOR ESPECÍFICO ITAÚ (LEITURA ESPACIAL)
# ==========================================

# Keywords que marcam o FIM da seção de movimentação
_ITAU_STOP = [
    "Cheque Especial",
    "Limite contratado",
    "Notas explicativas",
    "Taxa de juros",
]

# Linhas de saldo corrente exibidas no final de cada grupo de datas
_SKIP_SALDO_RE = re.compile(r"^Saldo\s+(em\s+C/C|final)", re.IGNORECASE)

# Remove legendas do sidebar esquerdo mescladas com transações pelo layout=True
# Ex: "P = poupança automática BOLETO PAGO DISPROPAN LT 2.972,38-"
#      ^^^^^^^^^^^^^^^^^^^^^^^^^^^^  <- sidebar, removido
_SIDEBAR_RE = re.compile(r"^[A-Z]\s*=\s*(?:[^A-Z\n]){3,50}(?=[A-Z])", re.UNICODE)


@st.cache_data(show_spinner=False)
def extrair_pdf_itau(file_bytes):
    dados, ignoradas_raw = [], []
    try:
        # ----------------------------------------------------------------
        # ATENÇÃO: in_movimento e current_date ficam FORA do loop de páginas
        # porque o extrato Itaú continua nas páginas seguintes sem repetir
        # o cabeçalho "Movimentação".
        # ----------------------------------------------------------------
        in_movimento = False
        current_date = None
        ano = str(pd.Timestamp.now().year)

        with pdfplumber.open(io.BytesIO(file_bytes)) as pdf:
            # Descobre o ano uma vez percorrendo as primeiras páginas
            for page in pdf.pages:
                t = page.extract_text(layout=True)
                if t:
                    m = re.search(r"\b(20[2-9]\d)\b", t)
                    if m:
                        ano = m.group(1)
                        break

            for page in pdf.pages:
                texto = page.extract_text(layout=True)
                if not texto:
                    continue

                for linha in texto.split("\n"):
                    # --- Controle de seção ---
                    if "Movimentação" in linha and not in_movimento:
                        in_movimento = True
                        continue
                    if not in_movimento:
                        continue
                    if any(kw in linha for kw in _ITAU_STOP):
                        in_movimento = False
                        continue

                    # --- Filtros de ruído ---
                    if "Este material" in linha:
                        continue
                    if "data" in linha and "descrição" in linha:
                        continue  # cabeçalho da coluna

                    linha = linha.strip()
                    if not linha:
                        continue

                    # Saldo corrente do dia (última linha de cada grupo)
                    if _SKIP_SALDO_RE.match(linha):
                        continue

                    # Remove legenda do sidebar se estiver na mesma linha
                    linha = _SIDEBAR_RE.sub("", linha).strip()
                    if not linha:
                        continue

                    # --- Data DD/MM no início da linha ---
                    match_data = re.search(r"^(\d{2}/\d{2})\b", linha)
                    if match_data:
                        nova_data = f"{match_data.group(1)}/{ano}"
                        if "Saldo anterior" in linha:
                            current_date = nova_data
                            continue
                        current_date = nova_data
                        linha = linha[match_data.end() :].strip()

                    if not current_date:
                        continue

                    # --- Valores monetários: 1.234,56 ou 1.234,56- ---
                    v_list = list(
                        re.finditer(r"\d{1,3}(?:\.\d{3})*,\d{2}\s*-?", linha)
                    )
                    if not v_list:
                        ignoradas_raw.append(linha)
                        continue

                    # Quando há 2 valores, o último é o saldo corrente → descarta
                    v_match = v_list[-2] if len(v_list) >= 2 else v_list[0]
                    valor_str = v_match.group().strip()
                    valor_num = float(
                        re.sub(r"[^\d,]", "", valor_str).replace(",", ".")
                    )

                    # Sinal: "-" no sufixo = saída; sem "-" = entrada
                    sinal = "-" if "-" in valor_str else "+"

                    desc_raw = linha[: v_match.start()].strip()
                    desc_limpa = padronizar_texto(desc_raw)
                    if not desc_limpa or len(desc_limpa) < 2:
                        desc_limpa = "SEM DESCRICAO"

                    dados.append(
                        {
                            "Data": current_date,
                            "Descricao": desc_limpa,
                            "Valor": abs(valor_num),
                            "Sinal": sinal,
                        }
                    )

    except Exception as e:
        logging.exception(f"Erro no Itaú: {e}")

    return pd.DataFrame(dados), {"criticas": [], "comuns": ignoradas_raw}


# ==========================================
# MOTOR ESPECÍFICO BANCO DO BRASIL (PLANILHAS)
# ==========================================


@st.cache_data(show_spinner=False)
def extrair_planilha_bb(file_bytes, nome_arquivo):
    """
    Lê extratos em Excel ou CSV exportados pelo Banco do Brasil.

    Estrutura esperada do arquivo:
      Linha 1: "Extrato Conta Corrente"        → ignorada
      Linha 2: Agência / Conta                 → ignorada
      Linha 3: cabeçalhos das colunas          ← skiprows=2 começa aqui
      Linha 4+: transações

    Colunas utilizadas:
      Data | Historico | Valor R$ | Inf. | Detalhamento Hist.

    Mesclagem Historico + Detalhamento:
      Todo texto numérico do campo Detalhamento é descartado;
      apenas textos alfabéticos são acrescentados ao Historico.
      Ex: "01/03 07:11 00003472918640 RAFAEL OTTE" → "RAFAEL OTTE"
    """
    try:
        if nome_arquivo.lower().endswith(".csv"):
            try:
                df_raw = pd.read_csv(
                    io.BytesIO(file_bytes), sep=",", skiprows=2
                )
                if (
                    "Valor R$ " not in df_raw.columns
                    and "Valor" not in df_raw.columns
                ):
                    df_raw = pd.read_csv(
                        io.BytesIO(file_bytes), sep=";", skiprows=2
                    )
            except Exception:
                df_raw = pd.read_csv(
                    io.BytesIO(file_bytes), sep=";", skiprows=2
                )
        else:
            df_raw = pd.read_excel(io.BytesIO(file_bytes), skiprows=2)

        df_raw.columns = [str(c).strip() for c in df_raw.columns]

        col_data   = "Data"              if "Data"              in df_raw.columns else df_raw.columns[0]
        col_hist   = "Historico"         if "Historico"         in df_raw.columns else None
        col_detalhe= "Detalhamento Hist."if "Detalhamento Hist." in df_raw.columns else None
        col_valor  = "Valor R$"          if "Valor R$"          in df_raw.columns else (
                     "Valor"             if "Valor"             in df_raw.columns else None)
        col_sinal  = "Inf."              if "Inf."              in df_raw.columns else None

        dados = []
        if col_hist and col_valor:
            for _, row in df_raw.iterrows():
                data_raw = str(row[col_data]).strip()
                # Aceita DD/MM/YYYY e DD/MM/YY
                if not re.match(r"\d{2}/\d{2}/\d{2,4}", data_raw):
                    continue

                desc = str(row[col_hist]).strip()

                # Mescla Detalhamento removendo todos os números
                if (
                    col_detalhe
                    and pd.notna(row[col_detalhe])
                    and str(row[col_detalhe]).strip() not in ("nan", "")
                ):
                    detalhe_str = str(row[col_detalhe]).strip()
                    detalhe_sem_numeros = re.sub(r"\d+", "", detalhe_str)
                    detalhe_limpo = re.sub(r"[^\w\s]", " ", detalhe_sem_numeros)
                    detalhe_limpo = re.sub(r"\s+", " ", detalhe_limpo).strip()
                    if detalhe_limpo:
                        desc += " " + detalhe_limpo

                valor_str = str(row[col_valor]).replace("R$", "").strip()
                if pd.isna(row[col_valor]) or valor_str == "nan":
                    continue

                valor_num = float(
                    valor_str.replace(".", "").replace(",", ".")
                )

                # Sinal determinado pela coluna Inf. (C = crédito, D = débito)
                if col_sinal and pd.notna(row[col_sinal]):
                    sinal = "+" if str(row[col_sinal]).strip().upper() == "C" else "-"
                else:
                    sinal = "+" if valor_num >= 0 else "-"

                dados.append(
                    {
                        "Data": data_raw,
                        "Descricao": padronizar_texto(desc),
                        "Valor": abs(valor_num),
                        "Sinal": sinal,
                    }
                )

        return pd.DataFrame(dados)

    except Exception as e:
        st.error(f"Erro ao processar a planilha {nome_arquivo}: {e}")
        logging.exception("Erro na extração Planilha BB")
        return pd.DataFrame()
