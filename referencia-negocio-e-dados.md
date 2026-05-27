# Referência de Negócio e Dados — Analytics Ecommerce

> Documento de referência para o próximo projeto. Contém contexto de negócio, schema completo das tabelas, KPIs, fórmulas de cálculo e alertas críticos sobre a qualidade dos dados.

---

## Contexto de Negócio

Dashboard analítico de um ecommerce brasileiro com três visões executivas:

| Visão | Objetivo |
|-------|----------|
| **Vendas & Receita** | Receita do dia, ticket médio, mix de canal, top produtos e categorias |
| **Pricing & Margem** | Posicionamento de preço vs. concorrentes, oportunidades de repricing |
| **Clientes & Comportamento** | Base ativa, taxa de ativação, distribuição geográfica, LTV |

**Snapshot**: todos os dados de vendas estão concentrados em **13/12/2025** — não há série temporal real.  
**Moeda**: BRL (R$), formatação `pt-BR`.  
**Canais de venda**: `ecommerce` e `loja_fisica` (sem cedilha — valor exato no banco).

---

## Schema das Tabelas (Supabase / PostgreSQL)

### `clientes` — 35 linhas

| Coluna | Tipo | Descrição |
|--------|------|-----------|
| `id_cliente` | text (PK) | Prefixo `cus_` + hash |
| `nome_cliente` | text | Nome completo (inclui tratamentos como "Srta.", "Dr.") |
| `estado` | text | UF (2 letras) |
| `pais` | text | Sempre `Brasil` no dataset atual |
| `data_cadastro` | timestamptz | Cadastros de 2022-12 a 2025-05 |

**Distribuição geográfica**: ~20 UFs representadas, média de ~1,75 clientes por UF. Estados com mais clientes: AM, MG e CE (3 cada). Ler como **mapa de presença**, não tamanho de mercado.

---

### `produtos` — 45 linhas

| Coluna | Tipo | Descrição |
|--------|------|-----------|
| `id_produto` | text (PK) | Prefixo `prd_` + hash |
| `nome_produto` | text | Nome do produto |
| `categoria` | text | Uma das 10 categorias abaixo |
| `marca` | text | Fabricante/marca |
| `preco` | numeric | Preço de venda |
| `data_cadastro` | timestamptz | Data de cadastro no sistema |

**Categorias e distribuição**:

| Categoria | Qtd produtos | Tem dado de concorrente? |
|-----------|:---:|:---:|
| Eletrônicos | 8 | Sim |
| Moda | 7 | Sim |
| Casa | 7 | Sim |
| Acessórios | 7 | Sim |
| Games | 5 | Sim |
| Beleza | 3 | Sim |
| Cozinha | 2 | **Não** |
| Áudio | 2 | **Não** |
| Esporte | 2 | **Não** |
| Informática | 2 | **Não** |

---

### `vendas` — 80 linhas

| Coluna | Tipo | Descrição |
|--------|------|-----------|
| `id_venda` | text (PK) | Prefixo `sal_` + hash |
| `data_venda` | timestamptz | **Todas em 13/12/2025** |
| `id_cliente` | text (FK → clientes) | |
| `id_produto` | text (FK → produtos) | |
| `canal_venda` | text | `ecommerce` ou `loja_fisica` |
| `quantidade` | integer | Unidades vendidas |
| `preco_unitario` | numeric | Retornado como string pelo Supabase — converter com `Number()` |

**Resumo financeiro**:
- Receita total: **R$ 50.169,43**
- E-commerce: 47 vendas / R$ 28.805
- Loja física: 33 vendas / R$ 21.363

---

### `preco_competidores` — 48 linhas

| Coluna | Tipo | Descrição |
|--------|------|-----------|
| `id` | bigint (PK) | Auto-incremento |
| `id_produto` | text (FK → produtos) | |
| `nome_concorrente` | text | Ex.: `Mercado Livre`, `Amazon`, `Magalu`, `Shopee` |
| `preco_concorrente` | numeric | Preço coletado |
| `data_coleta` | timestamptz | Data da coleta de preço |

**Cobertura parcial**: categorias Cozinha, Áudio, Esporte e Informática não têm nenhum registro. Mostrar "sem dado" — nunca tratar ausência como preço zero.

---

## Relacionamentos

```
clientes ──────────────────┐
                           ▼
                        vendas ◄──── produtos ◄──── preco_competidores
```

```sql
vendas.id_cliente             → clientes.id_cliente
vendas.id_produto             → produtos.id_produto
preco_competidores.id_produto → produtos.id_produto
```

`vendas` é a tabela **fato**. `clientes` e `produtos` são **dimensões**. `preco_competidores` é **dimensão satélite** de produtos.

---

## KPIs e Fórmulas

### Vendas & Receita

| KPI | Fórmula | Notas |
|-----|---------|-------|
| **Receita Total** | `Σ (quantidade × Number(preco_unitario))` | Converter `preco_unitario` para número antes |
| **Ticket Médio** | `Receita Total ÷ nº de pedidos` | Pedido = 1 linha de `vendas` |
| **Unidades Vendidas** | `Σ quantidade` | Inteiro; exibir `—` se não há pedidos |
| **Total de Pedidos** | `vendas.length` | Mostrar canal líder como subtítulo |

**Receita por venda** (helper reutilizável):
```ts
const valorVenda = (v) => (v.quantidade ?? 0) * Number(v.preco_unitario ?? 0)
```

**Gráficos**:

| Gráfico | Fórmula |
|---------|---------|
| Mix de Canal | Receita e pedidos por `canal_venda`; `percentual = receita ÷ receita_total` |
| Curva por Hora | Agrupar por hora de `data_venda`; preencher horas sem venda com `{ receita: 0, pedidos: 0 }` para não "pular" intervalos |
| Top 5 Produtos | `Σ receita` por `nome_produto`, ordenar desc, pegar top 5 |
| Receita por Categoria | `Σ receita` por `produtos.categoria`; `null` → `'sem categoria'` |

---

### Pricing & Margem

| KPI | Fórmula | Fonte |
|-----|---------|-------|
| **Índice de Competitividade** | `preco_produto ÷ média(preco_concorrente)` | `produtos` + `preco_competidores` |
| **Produtos Acima do Mercado** | nº de produtos com índice > 1,05 | Fora da banda saudável (cara) |
| **Produtos Abaixo do Mercado** | nº de produtos com índice < 0,95 | Fora da banda saudável (barato) |
| **Oportunidades de Repricing** | produtos com índice fora de `[0,95 ; 1,05]` | Soma dos dois acima |
| **Cobertura de Concorrente** | `nº produtos com ≥1 registro ÷ total produtos` | 6 de 10 categorias têm dado |

**Banda saudável de competitividade**: 0,95 a 1,05 (±5% do mercado).

**Atenção**: 4 categorias sem dado de concorrente → exibir estado "sem dado" explicitamente, nunca calcular índice como 0 ou 1.

---

### Clientes & Comportamento

| KPI | Fórmula | Notas |
|-----|---------|-------|
| **Clientes Cadastrados** | `clientes.length` | Total da tabela |
| **Clientes Ativos** | nº de clientes com `totalPedidos > 0` | Têm ao menos 1 venda |
| **Clientes Dormentes** | `cadastrados − ativos` | Sem nenhuma venda |
| **Taxa de Recompra** | `ativos com 2+ pedidos ÷ ativos × 100` | **Ver ressalva abaixo** |
| **Ticket Médio por Cliente** | `Σ receita dos ativos ÷ nº de ativos` | BRL |

**Gráficos**:

| Visão | Fórmula |
|-------|---------|
| Top 5 Clientes | Filtra ativos, ordena por `receitaTotal` desc, pega top 5 |
| Distribuição por Estado | nº de clientes e receita por `estado`; top 10 explícito + agrega restante como `'Outros'` |
| Canal Preferido | Canal com mais pedidos por cliente (empate → primeiro encontrado); pie ecommerce vs loja_fisica |
| Categorias Mais Compradas | `Σ quantidade` e `Σ receita` por `produtos.categoria` via join nas vendas |

---

## Alertas Críticos sobre os Dados

### 1. Todas as vendas em uma única data
Todas as 80 vendas estão em **13/12/2025**. Consequências:
- Gráficos de série temporal diária/mensal ficam **degenerados** — não construir.
- A "curva por hora" é válida como distribuição intradia do snapshot.
- "Taxa de Recompra" significa 2+ pedidos **no mesmo dia**, não retorno em datas distintas.

### 2. Cobertura parcial de concorrentes
Cozinha, Áudio, Esporte e Informática **não têm dados de `preco_competidores`**. Sempre checar `null`/ausência antes de calcular índice.

### 3. `preco_unitario` retorna como string
O Supabase pode retornar campos `numeric` como string. Sempre converter: `Number(preco_unitario ?? 0)`. Sem isso, ocorre **concatenação em vez de soma**.

### 4. Base pequena — cuidado com KPIs ruidosos
- 35 clientes distribuídos em ~20 UFs → médias por UF sem significância estatística.
- Ticket médio por estado com 1-3 vendas é anedota, não tendência.

### 5. RLS (Row Level Security) no Supabase
RLS habilitado em todas as tabelas. Policies `SELECT` para o papel `anon` já criadas. Se uma tabela aparecer vazia no cliente mas cheia no SQL Editor, verificar policies antes de buscar bug no código.

---

## Padrões de Query (Supabase / PostgREST)

### Join via embed
```ts
const { data, error } = await supabase
  .from('vendas')
  .select(`
    id_venda,
    quantidade,
    preco_unitario,
    canal_venda,
    data_venda,
    produtos:id_produto ( nome_produto, categoria, marca ),
    clientes:id_cliente ( nome_cliente, estado )
  `)
```

### Agregação em memória (aceitável para esta base)
```ts
const receitaPorCanal = vendas.reduce((acc, v) => {
  acc[v.canal_venda] = (acc[v.canal_venda] ?? 0) + (v.quantidade ?? 0) * Number(v.preco_unitario ?? 0)
  return acc
}, {})
```

### Tratamento de erro padrão
```ts
const { data, error } = await supabase.from('tabela').select('...')
if (error || !data) return [] // nunca lançar erro cru para o componente
```

### Fuso horário nas datas
Usar `Intl.DateTimeFormat` com `timeZone: 'America/Sao_Paulo'` para evitar deslocamento por fuso do browser ao exibir horas.

---

## Tipos TypeScript de Referência

```ts
type Cliente = {
  id_cliente: string
  nome_cliente: string
  estado: string
  pais: string
  data_cadastro: string // ISO
}

type Produto = {
  id_produto: string
  nome_produto: string
  categoria: string
  marca: string
  preco: number
  data_cadastro: string
}

type Venda = {
  id_venda: string
  data_venda: string
  id_cliente: string
  id_produto: string
  canal_venda: 'ecommerce' | 'loja_fisica'
  quantidade: number
  preco_unitario: number // atenção: pode vir como string do Supabase
}

type PrecoCompetidor = {
  id: number
  id_produto: string
  nome_concorrente: string
  preco_concorrente: number
  data_coleta: string
}
```

---

## Convenções de Formatação

| Tipo | Formato | Exemplo |
|------|---------|---------|
| Moeda | `pt-BR`, BRL, 2 casas | `R$ 50.169,43` |
| Percentual | 1 casa decimal | `12,3%` |
| Inteiro | `pt-BR` | `1.234` |
| Data | `dd/mm/aaaa` | `13/12/2025` |
| Canal | Humanizar para exibição | `loja_fisica` → `Loja Física` |
| Categoria nula | String literal | `null` → `'sem categoria'` |
| Estado nulo | String literal | `null` → `'N/D'` |

---

## Instância Supabase

- **Projeto**: `nmehophblsjjpcgreqzl.supabase.co`
- **Acesso**: read-only via `anon` key (sem INSERT/UPDATE/DELETE)
- **Variáveis de ambiente**: `NEXT_PUBLIC_SUPABASE_URL` e `NEXT_PUBLIC_SUPABASE_PUBLISHABLE_KEY`
