# TechPulse Jobs

Plataforma web de monitoramento de vagas de emprego tech em tempo real, com autenticação, web scraping de cinco fontes públicas, banco de dados relacional e dashboard interativo com filtros por fonte, senioridade e tecnologia.

---

## Estrutura do projeto

```
techpulse_jobs/
└── app.py          # Aplicação completa: scraping + API Flask + frontend
```

O projeto é composto por um único arquivo. Ao rodar, o Flask serve o frontend, executa o scraping e gerencia o banco de dados automaticamente.

---

## Tecnologias utilizadas

| Camada | Tecnologia |
|--------|-----------|
| Backend | Python 3, Flask |
| Scraping | Requests, Feedparser |
| Banco de dados | SQLite 3 (nativo do Python) |
| Frontend | HTML, CSS, JavaScript |
| Autenticação | Flask Sessions com senha em hash SHA-256 |

---

## Fontes de dados

| Fonte | Tipo | Foco |
|-------|------|------|
| RemoteOK | API JSON pública | Vagas remotas globais |
| Remotive | API JSON pública | Dev, DevOps, Data |
| Arbeitnow | API JSON pública | Europa e remoto |
| Jobicy | RSS público | Full-time tech |
| TheMuse | API JSON pública | Entry Level / Júnior |

TheMuse é a única fonte com filtro nativo de nível Entry Level, sendo a principal fonte de vagas para quem está começando na área.

---

## Funcionalidades

- Login com sessão e senha armazenada em hash SHA-256
- Web scraping automático de cinco fontes públicas
- Banco de dados SQLite com quatro tabelas e controle de duplicatas por URL
- Cache de cinco minutos para evitar requisições desnecessárias às fontes
- Classificação automática de senioridade pelo título da vaga
- Filtro por fonte, senioridade e tecnologia — combinados simultaneamente
- Busca por cargo ou empresa em tempo real
- Whitelist de mais de 100 linguagens e tecnologias para classificação das tags
- Links diretos para o anúncio original em cada vaga
- Dashboard com métricas de total de vagas, fontes ativas e tecnologia mais frequente
- Gráfico de top tecnologias clicável — filtra a lista ao clicar na barra
- Histórico de execuções do scraper registrado no banco

---

## Como executar

### Pré-requisitos

Python 3.8 ou superior. Baixe em [python.org](https://www.python.org/downloads/).

### 1. Abrir o terminal na pasta do projeto

**Windows:** segure Shift e clique com botão direito na pasta, depois clique em "Abrir janela do PowerShell aqui".

**Mac/Linux:** clique com botão direito na pasta e selecione "Novo Terminal na Pasta".

### 2. Criar o ambiente virtual

**Windows:**
```bash
python -m venv venv
venv\Scripts\activate
```

**Mac/Linux:**
```bash
python3 -m venv venv
source venv/bin/activate
```

Quando o venv estiver ativo, aparece `(venv)` no início da linha do terminal.

### 3. Instalar as dependências

```bash
pip install flask feedparser requests
```

### 4. Rodar a aplicação

```bash
python app.py
```

Na primeira execução, o arquivo `techpulse.db` é criado automaticamente na mesma pasta.

### 5. Acessar no navegador

```
http://localhost:5000
```

Para parar o servidor, pressione `Ctrl+C` no terminal. Para desativar o venv, digite `deactivate`.

### Credenciais de acesso

| E-mail | Senha |
|--------|-------|
| admin@tech.com | 123456 |
| aluno@facul.com | senha123 |

Para adicionar usuários, edite o dicionário `USERS` no início do `app.py` antes da primeira execução. Após rodar uma vez, os usuários ficam salvos no banco.

---

## Banco de dados

O arquivo `techpulse.db` é criado automaticamente ao rodar `python app.py`. Ele persiste entre reinicializações — as vagas já coletadas ficam salvas.

### Estrutura das tabelas

**users**
- id, email, senha_hash, criado_em

**jobs**
- id, titulo, empresa, url (único), fonte, data, salario, senioridade, coletado_em

**jobs_tags**
- job_id, tag

**scrape_log**
- id, fonte, total, executado

A URL de cada vaga é usada como chave única — execuções repetidas do scraper nunca duplicam registros no banco.

### Visualizar o banco

Para inspecionar os dados graficamente, instale o [DB Browser for SQLite](https://sqlitebrowser.org/dl/), abra o programa e carregue o arquivo `techpulse.db`.

---

## Classificação de senioridade

A senioridade é detectada automaticamente pelo título da vaga:

| Nível | Palavras-chave detectadas |
|-------|--------------------------|
| Estágio | intern, trainee, estágio, apprentice |
| Júnior | junior, jr, entry level, associate, graduate |
| Pleno | padrão quando nenhum outro nível é detectado |
| Sênior | senior, sr, staff, principal, lead, tech lead |
| Liderança | manager, director, head of, VP, CTO |

---

## Como adicionar uma nova fonte

Crie uma função seguindo o padrão abaixo e inclua-a em `get_all_jobs()`:

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
                "url":     item.get("url", ""),
                "fonte":   "NomeFonte",
                "data":    item.get("date", "")[:10],
                "salario": item.get("salary", "") or "—",
            })
    except Exception as e:
        print(f"[NomeFonte] {e}")
    return jobs
```

```python
def get_all_jobs():
    all_jobs = scrape_remoteok() + scrape_remotive() + ... + scrape_nomefonte()
```

---

## Limitações

- LinkedIn e Indeed bloqueiam scraping automatizado e não são suportados
- A classificação de senioridade é baseada em palavras-chave no título e pode ter imprecisões
- Vagas em português do Brasil são limitadas — as fontes atuais são majoritariamente em inglês
- O cache é armazenado em memória e resetado ao reiniciar o servidor, mas as vagas permanecem no banco
