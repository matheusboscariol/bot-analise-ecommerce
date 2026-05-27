# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

Bot Telegram de inteligência de dados para e-commerce. Todo o código reside em **um único arquivo `app.py`**.

**Stack:**
- Python + `python-telegram-bot` v20+ (async)
- Groq (LLM) com tool use para SQL dinâmico
- Supabase / PostgreSQL (read-only via `anon` key)

**Três capacidades do bot:**
1. **Chat livre** — responde qualquer pergunta consultando o banco via tool use (SQL gerado dinamicamente)
2. **Relatório executivo** — insights para 3 diretores: Comercial, CS e Pricing
3. **`/relatorio`** — comando que dispara o relatório automaticamente

## Running the App

```bash
pip install python-telegram-bot groq supabase python-dotenv
python app.py
```

**Variáveis de ambiente necessárias** (arquivo `.env.local`):
```
NEXT_PUBLIC_SUPABASE_URL=https://nmehophblsjjpcgreqzl.supabase.co
NEXT_PUBLIC_SUPABASE_PUBLISHABLE_KEY=<anon_key>
GROQ_API_KEY=<groq_key>
TELEGRAM_BOT_TOKEN=<token>
```

## Supabase — Banco de Dados

**Projeto:** `nmehophblsjjpcgreqzl` (região us-east-1, status ACTIVE_HEALTHY)

Acesso **read-only** via `anon` key. RLS habilitado — todas as tabelas têm policy `SELECT` para `anon`. Se uma tabela aparecer vazia no cliente mas cheia no SQL Editor, verificar policies antes de buscar bug no código.

### Tabelas e tamanhos

| Tabela | Linhas | Notas |
|--------|--------|-------|
| `clientes` | 35 | PK: `id_cliente` (prefixo `cus_`) |
| `produtos` | 45 | PK: `id_produto` (prefixo `prd_`) |
| `vendas` | 80 | Tabela fato; **todas as datas em 13/12/2025** |
| `preco_competidores` | 48 | Cobertura parcial — 4 categorias sem dados |

**Relacionamentos:** `vendas.id_cliente → clientes` / `vendas.id_produto → produtos` / `preco_competidores.id_produto → produtos`

### Armadilhas críticas dos dados

- `preco_unitario` na tabela `vendas` retorna como **string** do Supabase — sempre converter: `float(preco_unitario or 0)`. Sem isso ocorre concatenação em vez de soma.
- Todas as 80 vendas estão em **13/12/2025** — não há série temporal. Gráficos diários/mensais ficam degenerados; "curva por hora" é válida.
- `canal_venda` usa `loja_fisica` (sem cedilha) — valor exato no banco.
- Categorias Cozinha, Áudio, Esporte e Informática **não têm registros** em `preco_competidores` — exibir "sem dado", nunca calcular índice como 0 ou 1.

## KPIs do `/relatorio`

### Vendas & Receita (Diretor Comercial)
- Receita Total: `Σ (quantidade × float(preco_unitario))`
- Ticket Médio: `Receita ÷ nº de pedidos` (1 linha de vendas = 1 pedido)
- Mix de Canal: receita e pedidos por `canal_venda` com percentual
- Top 5 Produtos por receita

### Pricing & Margem (Diretor de Pricing)
- Índice de Competitividade: `preco_produto ÷ média(preco_concorrente)` por produto
- Banda saudável: **[0,95 ; 1,05]** — produtos fora disso são oportunidades de repricing
- Nunca calcular índice para categorias sem dados de concorrente

### Clientes & Comportamento (Diretor de CS)
- Clientes Ativos: com ≥ 1 venda; Dormentes: sem nenhuma venda
- Taxa de Recompra: clientes com ≥ 2 pedidos ÷ ativos (no mesmo dia — única data disponível)
- Distribuição por estado; ticket médio por cliente ativo

## Formatação de Saída (padrão pt-BR)

| Tipo | Formato |
|------|---------|
| Moeda | `R$ 50.169,43` |
| Percentual | `12,3%` |
| Canal | `loja_fisica` → `Loja Física` |
| Categoria nula | `'sem categoria'` |
| Estado nulo | `'N/D'` |

## Referências

- **PRD completo:** `.llm/PRD.md`
- **Schema, KPIs e regras de negócio:** `referencia-negocio-e-dados.md` — leitura obrigatória antes de qualquer query ou cálculo de KPI
