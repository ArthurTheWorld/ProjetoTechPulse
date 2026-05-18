# TechPulse Jobs 🟣

> Dashboard de vagas tech em tempo real com login, scraping de 5 fontes, filtros por tecnologia, senioridade e fonte.

---

## Visão Geral

O TechPulse Jobs é um pipeline de web scraping que coleta vagas de emprego tech de múltiplas fontes públicas e as exibe em um dashboard interativo com autenticação. O projeto foi desenvolvido como trabalho acadêmico para demonstrar coleta, processamento e visualização de dados em tempo real.

```
techpulse_jobs/
└── app.py          # Tudo em um arquivo: scraping + API Flask + frontend
```

---

## Tecnologias Utilizadas

| Camada | Tecnologia |
|--------|-----------|
| Backend | Python 3, Flask |
| Scraping | `requests`, `feedparser` |
| Frontend | HTML, CSS, JavaScript (vanilla) |
| Autenticação | Flask Sessions |
| Cache | In-memory (5 minutos) |

---

## Fontes de Dados

| Fonte | Tipo | Foco |
|-------|------|------|
| **RemoteOK** | API JSON pública | Vagas remotas globais |
| **Remotive** | API JSON pública | Dev, DevOps, Data |
| **Arbeitnow** | API JSON pública | Europa + remoto |
| **Jobicy** | RSS público | Full-time tech |
| **TheMuse** ⭐ | API JSON pública | **Entry Level / Júnior** |

> ⭐ TheMuse é a única fonte com filtro nativo de `Entry Level` — ideal para quem está começando na área.

---

## Funcionalidades

-  **Login com sessão** — autenticação simples com Flask Sessions
-  **Scraping ao vivo** — coleta vagas das 5 fontes a cada execução
-  **Cache de 5 minutos** — evita requisições desnecessárias às APIs
-  **Filtro por fonte** — RemoteOK, Remotive, Arbeitnow, Jobicy, TheMuse
-  **Filtro por senioridade** — Estágio, Júnior, Pleno, Sênior, Liderança
-  **Filtro por tecnologia** — clique nas barras do gráfico ou na sidebar
-  **Busca por cargo ou empresa** — busca em tempo real
-  **Classificação de tecnologias** — whitelist com +100 linguagens e frameworks
-  **Links diretos** — cada vaga abre o anúncio original no site da fonte
-  **Dashboard com métricas** — total de vagas, tag mais frequente, fontes ativas

---

## Como Executar

### Pré-requisitos

- Python 3.8 ou superior

### 1. Instalar dependências

```bash
pip install flask feedparser requests
```

### 2. Executar

```bash
python app.py
```

### 3. Acessar no navegador

```
http://localhost:5000
```

### Credenciais de acesso

| E-mail | Senha |
|--------|-------|
| `admin@tech.com` | `123456` |
| `aluno@facul.com` | `senha123` |

> Para adicionar usuários, edite o dicionário `USERS` no início do `app.py`.

---

## Como Funciona

### Pipeline de dados

```
Fontes públicas          Backend Flask          Frontend
─────────────────        ──────────────         ─────────────────
RemoteOK  (JSON) ──┐
Remotive  (JSON) ──┤     /api/jobs   ──────►   Dashboard
Arbeitnow (JSON) ──┼──►  /api/stats  ──────►   Gráficos
Jobicy    (RSS)  ──┤     /login               Filtros
TheMuse   (JSON) ──┘     /logout
```

### Classificação de senioridade

Detectada automaticamente pelo título da vaga:

| Nível | Palavras-chave detectadas |
|-------|--------------------------|
| Estágio | intern, trainee, estágio, apprentice |
| Júnior | junior, jr, entry level, associate, graduate |
| Pleno | *(padrão quando nenhum outro é detectado)* |
| Sênior | senior, sr, staff, principal, lead, tech lead |
| Liderança | manager, director, head of, VP, CTO |

### Cache

As vagas são armazenadas em memória e atualizadas a cada **5 minutos**, evitando sobrecarga nas APIs.

---

## Adicionando Novas Fontes

Crie uma função `scrape_nomefonte()` seguindo o padrão:

```python
def scrape_nomefonte():
    jobs = []
    try:
        r = requests.get("https://api.exemplo.com/jobs", headers=HEADERS, timeout=10)
        r.raise_for_status()
        for item in r.json().get("jobs", []):
            jobs.append({
                "titulo":  item.get("title", ""),
                "empresa": item.get("company", "—"),
                "tags":    item.get("tags", [])[:4],
                "url":     item.get("url", ""),  # URL direta para o anúncio
                "fonte":   "NomeFonte",
                "data":    item.get("date", "")[:10],
                "salario": item.get("salary", "") or "—",
            })
    except Exception as e:
        print(f"[NomeFonte] {e}")
    return jobs
```

Depois inclua em `get_all_jobs()`:

```python
all_jobs = scrape_remoteok() + ... + scrape_nomefonte()
```

---

## Limitações

- **LinkedIn e Indeed** bloqueiam scraping automatizado (retornam 403)
- O cache é perdido ao reiniciar o servidor
- A classificação de senioridade é baseada em palavras-chave — pode ter imprecisões
- Vagas em português do Brasil são limitadas (fontes majoritariamente em inglês)
