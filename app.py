import asyncio
import html
import json
import logging
import os

from dotenv import load_dotenv
from groq import Groq, BadRequestError as GroqBadRequestError, RateLimitError as GroqRateLimitError
from supabase import create_client, Client
from telegram import Update
from telegram.ext import ApplicationBuilder, CommandHandler, ContextTypes, MessageHandler, filters

# --- Config ---
load_dotenv(".env.local")

logging.basicConfig(format="%(asctime)s — %(levelname)s — %(message)s", level=logging.INFO)
log = logging.getLogger(__name__)

SUPABASE_URL = os.getenv("NEXT_PUBLIC_SUPABASE_URL")
SUPABASE_KEY = os.getenv("NEXT_PUBLIC_SUPABASE_PUBLISHABLE_KEY")
GROQ_API_KEY = os.getenv("GROQ_API_KEY")
TELEGRAM_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")

sb: Client = create_client(SUPABASE_URL, SUPABASE_KEY)
groq_client = Groq(api_key=GROQ_API_KEY)
GROQ_MODEL = "llama-3.3-70b-versatile"

# ─────────────────────────────────────────────
# KPI FUNCTIONS — Python computa tudo aqui
# LLM nunca faz aritmética com dados brutos
# ─────────────────────────────────────────────

CANAL_LABEL = {"ecommerce": "E-commerce", "loja_fisica": "Loja Física"}

def fmt_brl(valor: float) -> str:
    s = f"{valor:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")
    return f"R$ {s}"

def fmt_pct(valor: float) -> str:
    return f"{valor:.1f}%".replace(".", ",")


def fetch_dados_vendas() -> dict:
    vendas = sb.from_("vendas").select("*, produtos(nome_produto, categoria)").execute().data

    receita_total = sum(v["quantidade"] * float(v["preco_unitario"] or 0) for v in vendas)
    total_pedidos = len(vendas)
    ticket_medio = receita_total / total_pedidos if total_pedidos else 0.0

    canal_receita: dict[str, float] = {}
    canal_pedidos: dict[str, int] = {}
    produto_receita: dict[str, float] = {}
    cat_receita: dict[str, float] = {}

    for v in vendas:
        canal = v["canal_venda"]
        rec_v = v["quantidade"] * float(v["preco_unitario"] or 0)
        canal_receita[canal] = canal_receita.get(canal, 0.0) + rec_v
        canal_pedidos[canal] = canal_pedidos.get(canal, 0) + 1

        nome = (v.get("produtos") or {}).get("nome_produto", "Desconhecido")
        produto_receita[nome] = produto_receita.get(nome, 0.0) + rec_v

        cat = (v.get("produtos") or {}).get("categoria") or "sem categoria"
        cat_receita[cat] = cat_receita.get(cat, 0.0) + rec_v

    top_produtos = sorted(produto_receita.items(), key=lambda x: x[1], reverse=True)[:10]
    top_categorias = sorted(cat_receita.items(), key=lambda x: x[1], reverse=True)[:5]

    return {
        "receita_total": round(receita_total, 2),
        "receita_total_fmt": fmt_brl(receita_total),
        "ticket_medio": round(ticket_medio, 2),
        "ticket_medio_fmt": fmt_brl(ticket_medio),
        "total_pedidos": total_pedidos,
        "canal": {
            CANAL_LABEL.get(k, k): {
                "receita_fmt": fmt_brl(v),
                "pedidos": canal_pedidos.get(k, 0),
                "pct_fmt": fmt_pct(v / receita_total * 100) if receita_total else "0%",
            }
            for k, v in sorted(canal_receita.items(), key=lambda x: x[1], reverse=True)
        },
        "top_10_produtos": [
            {"posicao": i + 1, "nome": n, "receita_fmt": fmt_brl(r)}
            for i, (n, r) in enumerate(top_produtos)
        ],
        "top_5_categorias": [
            {"posicao": i + 1, "categoria": c, "receita_fmt": fmt_brl(r)}
            for i, (c, r) in enumerate(top_categorias)
        ],
    }


def fetch_dados_clientes() -> dict:
    clientes = sb.from_("clientes").select("id_cliente, nome_cliente, estado").execute().data
    vendas = sb.from_("vendas").select("id_cliente, quantidade, preco_unitario").execute().data

    vpc: dict[str, dict] = {}
    for v in vendas:
        cid = v["id_cliente"]
        rec_v = v["quantidade"] * float(v["preco_unitario"] or 0)
        if cid not in vpc:
            vpc[cid] = {"pedidos": 0, "receita": 0.0}
        vpc[cid]["pedidos"] += 1
        vpc[cid]["receita"] += rec_v

    ativos = [c for c in clientes if c["id_cliente"] in vpc]
    dormentes_lst = [c["nome_cliente"] for c in clientes if c["id_cliente"] not in vpc]
    com_recompra = [c for c in ativos if vpc[c["id_cliente"]]["pedidos"] >= 2]
    taxa_recompra = len(com_recompra) / len(ativos) * 100 if ativos else 0.0
    receita_ativos = sum(vpc[c["id_cliente"]]["receita"] for c in ativos)
    ticket_medio = receita_ativos / len(ativos) if ativos else 0.0

    ranking = sorted(
        [
            {
                "nome": c["nome_cliente"],
                "estado": c.get("estado") or "N/D",
                "receita_fmt": fmt_brl(vpc[c["id_cliente"]]["receita"]),
                "pedidos": vpc[c["id_cliente"]]["pedidos"],
            }
            for c in ativos
        ],
        key=lambda x: float(x["receita_fmt"].replace("R$ ", "").replace(".", "").replace(",", ".")),
        reverse=True,
    )

    recompra_lst = [
        {"nome": c["nome_cliente"], "pedidos": vpc[c["id_cliente"]]["pedidos"]}
        for c in sorted(com_recompra, key=lambda x: vpc[x["id_cliente"]]["pedidos"], reverse=True)
    ]

    estado_count: dict[str, int] = {}
    for c in clientes:
        est = c.get("estado") or "N/D"
        estado_count[est] = estado_count.get(est, 0) + 1
    top_estados = sorted(estado_count.items(), key=lambda x: x[1], reverse=True)[:10]

    return {
        "total_cadastrados": len(clientes),
        "total_ativos": len(ativos),
        "total_dormentes": len(dormentes_lst),
        "taxa_recompra_fmt": fmt_pct(taxa_recompra),
        "ticket_medio_cliente_fmt": fmt_brl(ticket_medio),
        "ranking_clientes": ranking,          # todos os ativos ordenados por receita
        "clientes_com_recompra": recompra_lst,  # clientes com 2+ pedidos
        "top_10_estados": [{"estado": e, "clientes": n} for e, n in top_estados],
    }


def fetch_dados_pricing() -> dict:
    produtos = sb.from_("produtos").select("id_produto, nome_produto, categoria, preco").execute().data
    concorrentes = sb.from_("preco_competidores").select("id_produto, nome_concorrente, preco_concorrente").execute().data

    conc_por_produto: dict[str, list] = {}
    for c in concorrentes:
        conc_por_produto.setdefault(c["id_produto"], []).append(float(c["preco_concorrente"] or 0))

    resultado = []
    sem_dado = 0

    for p in produtos:
        precos = conc_por_produto.get(p["id_produto"])
        preco = float(p["preco"] or 0)

        if not precos:
            sem_dado += 1
            resultado.append({
                "nome": p["nome_produto"],
                "categoria": p["categoria"],
                "preco_fmt": fmt_brl(preco),
                "status": "sem_dado_concorrente",
            })
            continue

        media = sum(precos) / len(precos)
        indice = round(preco / media, 3) if media else 1.0
        dif_pct = round((indice - 1) * 100, 1)

        if indice > 1.05:
            status = "acima_do_mercado"
        elif indice < 0.95:
            status = "abaixo_do_mercado"
        else:
            status = "competitivo"

        resultado.append({
            "nome": p["nome_produto"],
            "categoria": p["categoria"],
            "preco_fmt": fmt_brl(preco),
            "media_concorrentes_fmt": fmt_brl(media),
            "indice": indice,
            "diferenca_pct": f"{'+' if dif_pct > 0 else ''}{dif_pct}%",
            "status": status,
        })

    resultado.sort(key=lambda x: abs(x.get("indice", 1) - 1), reverse=True)
    analisados = [r for r in resultado if r["status"] != "sem_dado_concorrente"]

    return {
        "total_produtos": len(produtos),
        "com_dados_concorrente": len(analisados),
        "sem_dado_concorrente": sem_dado,
        "acima_do_mercado": sum(1 for r in analisados if r["status"] == "acima_do_mercado"),
        "abaixo_do_mercado": sum(1 for r in analisados if r["status"] == "abaixo_do_mercado"),
        "competitivos": sum(1 for r in analisados if r["status"] == "competitivo"),
        "todos_os_produtos": resultado,
        "nota": "Índice = preco_produto / media_concorrentes. Banda saudável [0.95, 1.05]. Categorias sem dado: Cozinha, Áudio, Esporte, Informática.",
    }


def listar_catalogo(tabela: str, filtros: dict = None) -> dict:
    """Lookup simples em tabelas de catálogo (produtos, clientes) — sem cálculos."""
    allowed = {"produtos", "clientes"}
    if tabela not in allowed:
        return {"erro": f"Tabela '{tabela}' não permitida neste tool. Use resumo_vendas, ranking_clientes ou analise_precos."}
    try:
        q = sb.from_(tabela).select("*")
        for col, val in (filtros or {}).items():
            q = q.eq(col, val)
        data = q.limit(500).execute().data
        return {"total": len(data), "dados": data}
    except Exception as e:
        return {"erro": str(e)}


# ─────────────────────────────────────────────
# TOOLS — a LLM escolhe; Python executa e calcula
# ─────────────────────────────────────────────

TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "resumo_vendas",
            "description": (
                "Retorna análise COMPLETA de vendas com todos os cálculos prontos: "
                "receita total, ticket médio, total de pedidos, mix de canal (E-commerce vs Loja Física "
                "com receita e percentual), top 10 produtos por receita, top 5 categorias por receita. "
                "Usar para QUALQUER pergunta sobre: faturamento, receita, total em vendas, ticket médio, "
                "produto mais vendido, canal líder, categoria mais lucrativa."
            ),
            "parameters": {"type": "object", "properties": {}, "required": []},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "ranking_clientes",
            "description": (
                "Retorna análise COMPLETA de clientes com todos os cálculos prontos: "
                "ranking de todos os clientes ativos por receita (com nome, receita formatada, nº de pedidos), "
                "lista de clientes com recompra (2+ pedidos), taxa de recompra, "
                "ticket médio por cliente, distribuição por estado. "
                "Usar para QUALQUER pergunta sobre: top clientes, clientes que mais compraram, "
                "clientes com recompra, clientes ativos/dormentes, perfil geográfico."
            ),
            "parameters": {"type": "object", "properties": {}, "required": []},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "analise_precos",
            "description": (
                "Retorna análise COMPLETA de competitividade de preços com todos os cálculos prontos: "
                "para cada produto com dados, índice = preco_produto / media_concorrentes, "
                "status (acima_do_mercado, competitivo, abaixo_do_mercado, sem_dado_concorrente), "
                "diferença percentual. Banda saudável: [0.95, 1.05]. "
                "Usar para QUALQUER pergunta sobre: preço competitivo, repricing, comparação com concorrência, "
                "produtos mais caros/baratos que o mercado."
            ),
            "parameters": {"type": "object", "properties": {}, "required": []},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "listar_catalogo",
            "description": (
                "Lista registros de catálogo (produtos ou clientes) SEM cálculos. "
                "Usar APENAS para: listar produtos de uma categoria, buscar produto por nome, "
                "listar clientes de um estado. NÃO usar para análises financeiras."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "tabela": {
                        "type": "string",
                        "enum": ["produtos", "clientes"],
                        "description": "Tabela de catálogo a consultar",
                    },
                    "filtros": {
                        "type": "object",
                        "description": "Filtros de igualdade opcionais, ex: {'categoria': 'Eletrônicos'}",
                    },
                },
                "required": ["tabela"],
            },
        },
    },
]


def despachar_tool(nome: str, args: dict) -> str:
    try:
        if nome == "resumo_vendas":
            return json.dumps(fetch_dados_vendas(), ensure_ascii=False)
        elif nome == "ranking_clientes":
            return json.dumps(fetch_dados_clientes(), ensure_ascii=False)
        elif nome == "analise_precos":
            return json.dumps(fetch_dados_pricing(), ensure_ascii=False)
        elif nome == "listar_catalogo":
            return json.dumps(listar_catalogo(args.get("tabela", ""), args.get("filtros")), ensure_ascii=False)
        else:
            return json.dumps({"erro": f"Tool desconhecido: {nome}"})
    except Exception as e:
        return json.dumps({"erro": str(e)})


# ─────────────────────────────────────────────
# SYSTEM PROMPT
# ─────────────────────────────────────────────

SYSTEM_PROMPT = """Você é um assistente analítico de um e-commerce brasileiro. Responda em português do Brasil com valores em BRL (ex: R$ 1.234,56).

Você tem 4 ferramentas disponíveis. Todas as ferramentas já retornam os dados calculados e formatados — você NUNCA precisa fazer cálculos:

1. **resumo_vendas** — receita total, ticket médio, top produtos, top categorias, mix de canal. Use para qualquer pergunta sobre vendas ou faturamento.
2. **ranking_clientes** — ranking por receita, clientes com recompra, distribuição por estado. Use para qualquer pergunta sobre clientes.
3. **analise_precos** — índice de competitividade por produto (preco/media_concorrentes), status e diferença %. Use para qualquer pergunta sobre preços ou concorrência.
4. **listar_catalogo** — lista produtos ou clientes com filtros. Use só para consultas de catálogo simples.

Regras:
- SEMPRE use uma ferramenta antes de responder — nunca invente ou estime valores.
- Use os valores já formatados nos campos `_fmt` da resposta da ferramenta.
- Não mostre contas ou cálculos na resposta — apenas os resultados.
- Seja direto: apresente os dados solicitados de forma clara e concisa.
"""


# ─────────────────────────────────────────────
# TOOL USE LOOP
# ─────────────────────────────────────────────

def loop_tool_use(messages: list) -> str:
    for _ in range(5):
        try:
            response = groq_client.chat.completions.create(
                model=GROQ_MODEL,
                messages=messages,
                tools=TOOLS,
                tool_choice="auto",
            )
        except GroqRateLimitError as e:
            log.warning("Rate limit Groq: %s", e)
            import re as _re
            wait = _re.search(r"try again in ([^\.]+)", str(e))
            tempo = wait.group(1) if wait else "alguns minutos"
            raise RuntimeError(f"⏳ Limite diário de tokens do Groq atingido. Tente novamente em {tempo}.")
        except GroqBadRequestError as e:
            log.warning("Tool call inválido (400), retentando: %s", e)
            messages.append({
                "role": "user",
                "content": "Erro na chamada anterior. Use apenas os tools disponíveis: resumo_vendas, ranking_clientes, analise_precos, listar_catalogo. Tente novamente.",
            })
            continue

        msg = response.choices[0].message

        if not msg.tool_calls:
            return msg.content or ""

        messages.append({
            "role": "assistant",
            "content": msg.content,
            "tool_calls": [
                {
                    "id": tc.id,
                    "type": "function",
                    "function": {"name": tc.function.name, "arguments": tc.function.arguments},
                }
                for tc in msg.tool_calls
            ],
        })

        for tc in msg.tool_calls:
            args = json.loads(tc.function.arguments)
            resultado = despachar_tool(tc.function.name, args)
            messages.append({"role": "tool", "tool_call_id": tc.id, "content": resultado})

    return "Não consegui processar sua pergunta. Tente reformulá-la."


# ─────────────────────────────────────────────
# RELATÓRIO EXECUTIVO
# ─────────────────────────────────────────────

def _groq_insight(prompt: str) -> str:
    try:
        return groq_client.chat.completions.create(
            model=GROQ_MODEL,
            messages=[{"role": "user", "content": prompt}],
            max_tokens=180,
        ).choices[0].message.content.strip()
    except Exception:
        return ""


def gerar_relatorio() -> list[str]:
    v = fetch_dados_vendas()
    p = fetch_dados_pricing()
    c = fetch_dados_clientes()

    # ---- Seção 1: Vendas & Receita ----
    canal_txt = "".join(
        f"  • {canal}: {dados['receita_fmt']} ({dados['pct_fmt']}) — {dados['pedidos']} pedidos\n"
        for canal, dados in v["canal"].items()
    )
    top5_txt = "\n".join(
        f"  {p['posicao']}. {html.escape(p['nome'])}: {p['receita_fmt']}"
        for p in v["top_10_produtos"][:5]
    )
    canal_lider = next(iter(v["canal"]))
    insight1 = _groq_insight(
        f"Dados de vendas (e-commerce, snapshot 13/12/2025): receita total {v['receita_total_fmt']}, "
        f"{v['total_pedidos']} pedidos, ticket médio {v['ticket_medio_fmt']}, canal líder {canal_lider}. "
        "Escreva 2 insights acionáveis em português para o Diretor Comercial. Máximo 70 palavras, tom executivo direto."
    )
    msg1 = (
        f"📊 <b>VENDAS &amp; RECEITA — Diretor Comercial</b>\n\n"
        f"💰 Receita Total: <b>{v['receita_total_fmt']}</b>\n"
        f"🛒 Pedidos: {v['total_pedidos']} | Ticket Médio: {v['ticket_medio_fmt']}\n\n"
        f"<b>Mix de Canal:</b>\n{canal_txt}\n"
        f"<b>Top 5 Produtos:</b>\n{top5_txt}"
    )
    if insight1:
        msg1 += f"\n\n💡 <i>{html.escape(insight1)}</i>"

    # ---- Seção 2: Pricing & Margem ----
    oport = [r for r in p["todos_os_produtos"] if r["status"] in ("acima_do_mercado", "abaixo_do_mercado")][:5]
    oport_txt = ""
    for o in oport:
        emoji = "🔴" if o["status"] == "acima_do_mercado" else "🟢"
        oport_txt += (
            f"  {emoji} {html.escape(o['nome'])}: {o['preco_fmt']} "
            f"(mercado: {o['media_concorrentes_fmt']}, {o['diferenca_pct']})\n"
        )
    insight2 = _groq_insight(
        f"Pricing: {p['com_dados_concorrente']} produtos analisados, "
        f"{p['acima_do_mercado']} acima do mercado (>5%), {p['abaixo_do_mercado']} abaixo (<5%), "
        f"{p['sem_dado_concorrente']} sem dados. "
        "Escreva 2 insights acionáveis em português para o Diretor de Pricing. Máximo 70 palavras, tom executivo direto."
    )
    msg2 = (
        f"💰 <b>PRICING &amp; MARGEM — Diretor de Pricing</b>\n\n"
        f"🔍 Analisados: {p['com_dados_concorrente']} | Sem dado: {p['sem_dado_concorrente']}\n"
        f"📈 Acima do mercado (&gt;5%): {p['acima_do_mercado']} produtos\n"
        f"📉 Abaixo do mercado (&lt;5%): {p['abaixo_do_mercado']} produtos\n"
        f"⚡ Oportunidades de repricing: <b>{p['acima_do_mercado'] + p['abaixo_do_mercado']}</b>\n\n"
        f"<b>Maiores Desvios:</b>\n{oport_txt or '  Todos dentro da banda ±5%'}"
    )
    if insight2:
        msg2 += f"\n💡 <i>{html.escape(insight2)}</i>"

    # ---- Seção 3: Clientes & Comportamento ----
    top5c_txt = "\n".join(
        f"  {i + 1}. {html.escape(r['nome'])}: {r['receita_fmt']} ({r['pedidos']} pedido{'s' if r['pedidos'] > 1 else ''})"
        for i, r in enumerate(c["ranking_clientes"][:5])
    )
    estados_txt = " | ".join(f"{e['estado']}: {e['clientes']}" for e in c["top_10_estados"])
    insight3 = _groq_insight(
        f"Clientes: {c['total_cadastrados']} cadastrados, {c['total_ativos']} ativos, "
        f"{c['total_dormentes']} dormentes, taxa de recompra {c['taxa_recompra_fmt']}, "
        f"ticket médio por cliente {c['ticket_medio_cliente_fmt']}. "
        "Escreva 2 insights acionáveis em português para o Diretor de CS. Máximo 70 palavras, tom executivo direto."
    )
    msg3 = (
        f"👥 <b>CLIENTES &amp; COMPORTAMENTO — Diretor de CS</b>\n\n"
        f"👤 Cadastrados: {c['total_cadastrados']} | Ativos: {c['total_ativos']} | Dormentes: {c['total_dormentes']}\n"
        f"🔄 Taxa de Recompra: <b>{c['taxa_recompra_fmt']}</b>\n"
        f"💳 Ticket Médio/Cliente: {c['ticket_medio_cliente_fmt']}\n\n"
        f"<b>Top 5 Clientes:</b>\n{top5c_txt}\n\n"
        f"<b>Distribuição por Estado:</b>\n  {estados_txt}"
    )
    if insight3:
        msg3 += f"\n\n💡 <i>{html.escape(insight3)}</i>"

    return [msg1, msg2, msg3]


# ─────────────────────────────────────────────
# TELEGRAM HANDLERS
# ─────────────────────────────────────────────

async def start_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "👋 Olá! Sou o assistente de dados do e-commerce.\n\n"
        "Você pode:\n"
        "• Fazer perguntas em linguagem natural sobre vendas, clientes e produtos\n"
        "• Digitar /relatorio para o relatório executivo completo\n\n"
        "Exemplos:\n"
        "— Quais os top 5 clientes por receita?\n"
        "— Qual o total em vendas?\n"
        "— Quais produtos estão com preço acima do mercado?\n"
        "— Quais clientes compraram mais de uma vez?"
    )


async def relatorio_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("⏳ Gerando relatório executivo...")
    try:
        mensagens = await asyncio.to_thread(gerar_relatorio)
        for msg in mensagens:
            await update.message.reply_html(msg)
    except Exception:
        log.exception("Erro ao gerar relatório")
        await update.message.reply_text("❌ Erro ao gerar o relatório. Tente novamente.")


async def mensagem_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await context.bot.send_chat_action(update.effective_chat.id, "typing")
    messages = [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": update.message.text},
    ]
    try:
        resposta = await asyncio.to_thread(loop_tool_use, messages)
        await update.message.reply_text(resposta)
    except RuntimeError as e:
        await update.message.reply_text(str(e))
    except Exception:
        log.exception("Erro no chat")
        await update.message.reply_text("❌ Erro ao processar sua mensagem. Tente novamente.")


# ─────────────────────────────────────────────
# MAIN
# ─────────────────────────────────────────────

def main():
    missing = [k for k, v in {
        "SUPABASE_URL": SUPABASE_URL,
        "SUPABASE_KEY": SUPABASE_KEY,
        "GROQ_API_KEY": GROQ_API_KEY,
        "TELEGRAM_BOT_TOKEN": TELEGRAM_TOKEN,
    }.items() if not v]
    if missing:
        raise ValueError(f"Variáveis de ambiente ausentes: {', '.join(missing)}")

    app = ApplicationBuilder().token(TELEGRAM_TOKEN).build()
    app.add_handler(CommandHandler("start", start_handler))
    app.add_handler(CommandHandler("relatorio", relatorio_handler))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, mensagem_handler))

    log.info("Bot iniciado. Pressione Ctrl+C para parar.")
    app.run_polling()


if __name__ == "__main__":
    main()
