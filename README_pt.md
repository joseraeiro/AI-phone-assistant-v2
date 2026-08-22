# Agente Telefónico Pessoal com IA

## Visão geral

Esta aplicação Python efetua chamadas telefónicas de saída através da Twilio e conduz conversas de voz orientadas para um objetivo utilizando a OpenAI Realtime API. Para cada chamada, o operador fornece um destino, objetivo, contexto, preferências e restrições. A aplicação disponibiliza um pequeno painel renderizado no servidor, histórico persistente de chamadas, atualizações da transcrição em tempo real, factos registados, um resumo estruturado após a chamada e gravação opcional.

O agente pode recolher e esclarecer informações de forma autónoma, mas não assume compromissos em nome do utilizador, a menos que essa ação seja explicitamente autorizada para a chamada em questão. Identifica-se como um assistente virtual a atuar em nome do utilizador e utiliza, por predefinição, português europeu (`pt-PT`).

## Arquitetura

```text
Navegador ──HTTP/SSE──> FastAPI ───────────────> SQLite
                           │                         ▲
                           │ Twilio REST             │ histórico de chamadas
                           v                         │
                        Twilio Voice <──PSTN──> telefone remoto
                           │
                    Media Stream bidirecional
                           │ WSS (JSON + base64 PCMU)
                           v
                        FastAPI <────WebSocket────> OpenAI Realtime
                           │
                           ├──Responses API───────> resumo pós-chamada
                           └──Twilio Recording API> gravação opcional
```

O FastAPI gere ambas as ligações de streaming e todas as credenciais. A Twilio efetua a chamada PSTN e transporta o áudio bidirecional. O OpenAI Realtime gere a voz, transcrição, deteção de turnos e respostas. O SQLite armazena a configuração das chamadas, transcrições finais, factos, eventos, resumos e metadados das gravações. A bridge encaminha diretamente áudio G.711 μ-law (`audio/pcmu`), 8 kHz, mono, em formato base64; não é efetuada qualquer transcodificação de áudio. Consulte [Arquitetura](docs/ARCHITECTURE.md) para obter detalhes sobre o ciclo de vida e interrupções.

## Requisitos

* Python 3.12 ou superior e [`uv`](https://docs.astral.sh/uv/)
* Uma conta Twilio e um número de telefone Twilio com capacidade de voz
* Uma chave da OpenAI API com acesso aos modelos configurados de Realtime, transcrição e resumo
* Um endereço público HTTPS/WSS para desenvolvimento local, por exemplo, ngrok

A aplicação não possui qualquer dependência de runtime específica do sistema operativo. As verificações automáticas de release são executadas em Linux; os comandos abaixo também incluem a configuração para PowerShell. As contas de teste da Twilio normalmente só conseguem ligar para números de destino verificados e poderão estar sujeitas a outras limitações do período experimental; consulte a Twilio Console caso uma chamada de teste seja rejeitada.

## Instalação

```bash
git clone <repository-url>
cd AI-phone-assistant-v2
uv sync
cp .env.example .env
```

No Windows PowerShell:

```powershell
git clone <repository-url>
Set-Location AI-phone-assistant-v2
uv sync
Copy-Item .env.example .env
```

Edite o `.env`; nunca faça commit deste ficheiro.

## Configuração do ambiente

Os valores vazios no `.env` são tratados como não definidos. As credenciais dos fornecedores permanecem exclusivamente no servidor.

| Variável                                  | Finalidade / valor predefinido                                                                                                                  |
| ----------------------------------------- | ----------------------------------------------------------------------------------------------------------------------------------------------- |
| `APP_BASE_URL`                            | Origem HTTPS pública, sem path; obrigatória para chamadas reais, por exemplo `https://example.ngrok-free.app`.                                  |
| `APP_HOST`, `APP_PORT`                    | Valores de bind para um comando Uvicorn explícito; predefinições `0.0.0.0`, `8000`.                                                             |
| `APP_TIMEZONE`                            | Fuso horário de apresentação reservado à formatação da interface; internamente, os dados são armazenados em UTC. Predefinição: `Europe/Lisbon`. |
| `LOG_LEVEL`                               | `DEBUG`, `INFO`, `WARNING`, `ERROR` ou `CRITICAL`; predefinição `INFO`.                                                                         |
| `DATABASE_URL`                            | URL assíncrono do SQLAlchemy; predefinição `sqlite+aiosqlite:///./ai_phone_assistant.db`.                                                       |
| `TWILIO_ACCOUNT_SID`                      | Account SID do projeto Twilio; obrigatório para chamadas reais.                                                                                 |
| `TWILIO_AUTH_TOKEN`                       | Twilio Auth Token; obrigatório para chamadas reais, validação de assinaturas e obtenção de gravações.                                           |
| `TWILIO_PHONE_NUMBER`                     | Caller ID Twilio com capacidade de voz, em formato E.164.                                                                                       |
| `TWILIO_VALIDATE_SIGNATURES`              | Valida assinaturas HTTP e WebSocket da Twilio; predefinição `true`. Desative apenas para testes locais controlados.                             |
| `OPENAI_API_KEY`                          | Chave da OpenAI API do lado do servidor; obrigatória para conversas reais e resumos.                                                            |
| `OPENAI_REALTIME_MODEL`                   | Modelo de voz Realtime; predefinição `gpt-realtime-2.1`.                                                                                        |
| `OPENAI_REALTIME_VOICE`                   | Voz Realtime; predefinição `marin`.                                                                                                             |
| `OPENAI_TRANSCRIPTION_MODEL`              | Modelo de transcrição de entrada; predefinição `gpt-live-transcribe`.                                                                           |
| `OPENAI_SUMMARY_MODEL`                    | Modelo de texto da Responses API para relatórios estruturados; predefinição `gpt-5.6-luna`.                                                     |
| `OPENAI_REALTIME_VAD_TYPE`                | `semantic_vad` (predefinição) ou `server_vad`.                                                                                                  |
| `OPENAI_REALTIME_VAD_EAGERNESS`           | Tempo de resposta do Semantic VAD: `low`, `medium`, `high` ou `auto`.                                                                           |
| `OPENAI_REALTIME_VAD_THRESHOLD`           | Limiar de voz do Server VAD; predefinição `0.5`.                                                                                                |
| `OPENAI_REALTIME_VAD_PREFIX_PADDING_MS`   | Áudio mantido antes de a fala ser detetada; predefinição `300`.                                                                                 |
| `OPENAI_REALTIME_VAD_SILENCE_DURATION_MS` | Silêncio de fim de turno do Server VAD; predefinição `700`.                                                                                     |
| `DEFAULT_RECORDING_POLICY`                | `off`, `ask` ou `always`; predefinição `ask`.                                                                                                   |
| `DOWNLOAD_RECORDINGS_LOCALLY`             | Guarda gravações concluídas em `RECORDINGS_DIR`; predefinição `false`.                                                                          |
| `RECORDINGS_DIR`                          | Diretório privado de gravações; predefinição `./data/recordings`.                                                                               |
| `DRY_RUN`                                 | Valida e guarda uma chamada simulada sem contactar os fornecedores; predefinição `false`.                                                       |

`uvicorn app.main:app --reload` utiliza os valores de bind predefinidos do próprio Uvicorn. Para aplicar explicitamente os valores configurados em Unix, execute `uv run uvicorn app.main:app --reload --host "$APP_HOST" --port "$APP_PORT"` depois de os exportar, ou forneça os valores diretamente.

## Configuração da Twilio

1. Na Twilio Console, copie o **Account SID** e o **Auth Token** para as respetivas variáveis no `.env`.
2. Em **Phone Numbers**, compre ou selecione um número com capacidade de Voice. Coloque esse número pertencente à Twilio, incluindo `+` e o indicativo do país, em `TWILIO_PHONE_NUMBER`.
3. Os utilizadores em período experimental devem verificar o telefone de destino na Console antes de efetuarem testes.
4. Inicie a aplicação e o túnel público descrito abaixo.

Não é necessário configurar qualquer webhook de Voice para chamadas recebidas no número Twilio. Para cada chamada de saída, a aplicação fornece dinamicamente os seguintes callbacks públicos:

* `POST /twilio/voice?call_id=<internal-uuid>` devolve TwiML com `<Connect><Stream>`;
* `POST /twilio/call-status?call_id=<internal-uuid>` recebe alterações ao ciclo de vida da chamada;
* `WSS /twilio/media` transporta o Media Stream bidirecional;
* `POST /twilio/recording-status?call_id=<internal-uuid>` recebe o estado da gravação.

A Twilio não consegue aceder a `localhost`, pelo que `APP_BASE_URL` tem de resolver publicamente através de HTTPS. A aplicação converte a origem `https://` em `wss://` para o Media Stream. Mantenha a validação de assinaturas ativa durante o funcionamento normal; o URL público exato que a Twilio assina tem de corresponder ao URL que o FastAPI reconstrói através do túnel/proxy.

## Túnel local

Com o [ngrok](https://ngrok.com/) instalado:

```bash
ngrok http 8000
```

Se o ngrok apresentar `Forwarding https://example.ngrok-free.app`, defina:

```env
APP_BASE_URL=https://example.ngrok-free.app
```

A aplicação fornece então à Twilio callbacks `https://example.ngrok-free.app/twilio/...` e `wss://example.ngrok-free.app/twilio/media`. Reinicie o FastAPI depois de alterar o `.env`. O Cloudflare Tunnel também pode ser utilizado, desde que forneça uma origem HTTPS pública estável e suporte encaminhamento de WebSockets.

## Inicialização da base de dados

Não é necessário criar manualmente a base de dados. Durante o arranque do FastAPI, o SQLAlchemy cria as tabelas SQLite em falta e aplica as pequenas adições de colunas de compatibilidade utilizadas nesta v1. Não existe qualquer comando Alembic nesta versão. O ficheiro de base de dados predefinido é `ai_phone_assistant.db`, na raiz do repositório; altere `DATABASE_URL` antes do primeiro arranque caso pretenda colocá-lo noutro local.

## Iniciar a aplicação

```bash
uv run uvicorn app.main:app --reload
```

Aceda a [http://localhost:8000](http://localhost:8000). `GET /health` verifica o SQLite e indica se a configuração da Twilio/OpenAI aparenta estar presente, sem contactar qualquer fornecedor pago nem devolver valores de credenciais.

Para explorar a interface sem efetuar chamadas pagas, defina `DRY_RUN=true`. Um dry run cria um registo de chamada simulada, mas nenhum telefone toca e não ocorre qualquer conversa Realtime.

## Tutorial da primeira chamada

Utilize o seu próprio telemóvel verificado para este teste básico:

```text
Nome do destino: O meu telemóvel
Número de telefone: +<indicativo-do-país><número>
Objetivo: Perguntar-me que horas são.
Contexto: Esta é uma chamada de teste.
Preferências: Manter a chamada curta.
Restrições: Não realizar qualquer ação além de fazer a pergunta.
Idioma: pt-PT
```

1. Confirme que o FastAPI e o túnel estão em execução e que `DRY_RUN=false`.
2. Abra o painel e selecione **New Call**.
3. Introduza os valores acima; o número tem de estar em formato E.164, como `+351` seguido do número de assinante.
4. Submeta o formulário. A criação solicita imediatamente a chamada de saída e abre a respetiva página de detalhes.
5. Atenda quando o telefone tocar. O agente deverá identificar-se, fazer a pergunta e conversar brevemente.
6. Termine a chamada normalmente ou utilize **End Call**.
7. Na página da chamada, consulte as entradas finais da transcrição, factos, eventos e o resumo gerado. A geração do resumo é assíncrona, pelo que a atualização da página/atualizações em tempo real poderão demorar algum tempo a aparecer.
8. Caso a gravação tenha sido ativada, utilize a secção Audio/Recording depois de a Twilio a marcar como concluída.

## Exemplo informativo do mundo real

```text
Nome do destino: Horto
Objetivo: Descobrir se têm atualmente plantas Echeveria.
Contexto: Talvez passe lá hoje.
Preferências: Perguntar que variedades têm e os tamanhos aproximados.
Restrições: Perguntar os preços. Não reservar nem comprar nada.
```

O agente pode esclarecer variedades, tamanhos, preços e disponibilidade. Caso lhe seja proposta uma reserva, deverá recusá-la, uma vez que a chamada não autorizou qualquer compromisso.

## Modelo de autoridade da chamada

```text
RECOLHA DE INFORMAÇÃO = PERMITIDA
COMPROMISSOS = NÃO PERMITIDOS, salvo autorização explícita para essa chamada
```

O comportamento permitido inclui perguntar sobre disponibilidade, preços, horários de funcionamento, requisitos, detalhes adicionais e números de referência. Por predefinição, o agente não pode marcar consultas, comprar produtos, reservar artigos, aceitar orçamentos, alterar contratos ou serviços, nem assumir compromissos financeiros. As instruções do agente e a configuração da chamada impõem este limite. Não existe qualquer workflow de aprovação por parte do utilizador.

## Gravação

* `off`: nunca inicia uma gravação Twilio.
* `ask`: o agente pergunta primeiro à pessoa do outro lado; a gravação só começa após consentimento claro e a chamada da ferramenta `start_recording_after_consent`.
* `always`: a gravação começa assim que o Media Stream é iniciado, sem qualquer passo de consentimento durante a conversa.

Os metadados da gravação — SID, estado, duração, canais e timestamps — são armazenados no SQLite. O browser obtém o WAV através de um endpoint proxy Twilio autenticado do lado do servidor; as credenciais da Twilio e os URLs de armazenamento não são expostos. Por predefinição, o áudio permanece na Twilio. Com `DOWNLOAD_RECORDINGS_LOCALLY=true`, os dados WAV das gravações concluídas também são guardados em `RECORDINGS_DIR` e servidos a partir daí quando existirem.

O operador é responsável por configurar e utilizar a gravação de acordo com a legislação aplicável. O software não determina se uma gravação é legal.

## Transcrição, gravação, resumo, eventos e factos

* **Transcrição:** texto final proveniente dos eventos de transcrição de entrada e saída do OpenAI Realtime. Os deltas parciais não são armazenados como centenas de registos.
* **Gravação:** áudio real da chamada telefónica, apenas quando a política de gravação a inicia.
* **Resumo:** relatório estruturado pós-chamada gerado através da Responses API a partir da transcrição, factos, objetivo da chamada e resultados das ferramentas.
* **Eventos:** timeline operacional relevante, e não telemetria de áudio por pacote.
* **Factos:** informação importante explicitamente registada pelo agente, incluindo o seu nível de confiança.

Pode existir uma transcrição sem gravação, e uma gravação pode ser concluída depois de a chamada telefónica terminar.

## Resolução de problemas e correlação de chamadas

Comece por `GET /health`, pelo log da aplicação, pelo log de pedidos do túnel, por **Monitor > Logs > Calls** na Twilio e pela timeline de eventos da página de detalhes da chamada. Verificações frequentes:

* **O telefone nunca toca:** valide as credenciais da Twilio, restrições de trial/geográficas, destino E.164, número do remetente e logs de chamadas da Twilio.
* **O telefone toca mas não há som:** confirme que `wss://.../twilio/media` estabeleceu ligação, que `OPENAI_API_KEY` é válida e que os logs mostram `MEDIA_STREAM_STARTED` e `OPENAI_REALTIME_CONNECTED`.
* **O WebSocket nunca estabelece ligação:** verifique `APP_BASE_URL`, suporte HTTPS/WSS do túnel, TwiML devolvido, logs do túnel e validação de assinaturas.
* **Áudio apenas num sentido:** a entrada deverá produzir contadores `media` da Twilio e eventos de entrada do OpenAI; a saída deverá produzir mensagens JSON `media`/`mark` da Twilio. O áudio utiliza PCMU/8 kHz/mono em ambos os sentidos.
* **Turnos lentos ou interrupções deficientes:** analise as definições de VAD e procure `ASSISTANT_INTERRUPTED`, `clear` da Twilio e processamento de marks antes de ajustar os valores.
* **Assinaturas 401/403:** faça com que o URL de callback assinado externamente e o URL visível através do proxy sejam idênticos; desative a validação apenas para um diagnóstico local controlado.
* **Transcrição incompleta:** o transporte de áudio e a transcrição são componentes separados; procure eventos finais de transcrição e erros do OpenAI.
* **Gravação indisponível:** verifique a política, o evento da ferramenta de consentimento no modo `ask`, o callback de gravação, o estado da gravação na Twilio e as credenciais.

Cada chamada possui um UUID interno na interface/base de dados. A Twilio acrescenta um Call SID e um Stream SID; as gravações acrescentam um Recording SID. Utilize estes identificadores juntamente com a timeline da chamada e os logs dos fornecedores. A aplicação não regista áudio em base64 nos logs. Consulte o [guia completo de resolução de problemas](docs/TROUBLESHOOTING.md).

## Logging e privacidade

`LOG_LEVEL=INFO` emite eventos importantes do ciclo de vida das chamadas, streams, bridge, resumos e gravações, sem criar uma linha de log por cada pacote de media. `LOG_LEVEL=DEBUG` acrescenta tipos de eventos de protocolo e diagnósticos de reprodução/bridge; ainda assim, não regista intencionalmente payloads de áudio base64, chaves de API, tokens de autenticação ou transcrições completas. Não ative logging HTTP detalhado de terceiros em produção e trate os ficheiros de log como dados operacionais sensíveis.

## Referência HTTP e WebSocket

| Método | Path                        | Finalidade                                                      |
| ------ | --------------------------- | --------------------------------------------------------------- |
| `GET`  | `/`                         | Painel de chamadas recentes                                     |
| `GET`  | `/calls/new`                | Formulário para nova chamada                                    |
| `POST` | `/calls`                    | Criar um registo de chamada (API JSON ou formulário do browser) |
| `GET`  | `/calls/{id}`               | Página de detalhes da chamada                                   |
| `POST` | `/calls/{id}/end`           | Terminar uma chamada ativa de forma idempotente                 |
| `GET`  | `/calls/{id}/events`        | Stream Server-Sent Events com snapshots em tempo real           |
| `POST` | `/calls/{id}/summary/retry` | Repetir a geração de um resumo falhado/em falta                 |
| `GET`  | `/calls/{id}/recording.wav` | Gravação concluída servida através do proxy do servidor         |
| `GET`  | `/health`                   | Estado da base de dados/configuração local                      |
| `POST` | `/twilio/voice`             | Webhook Twilio assinado após atendimento da chamada             |
| `POST` | `/twilio/call-status`       | Callback assinado do ciclo de vida                              |
| `POST` | `/twilio/recording-status`  | Callback assinado da gravação                                   |
| `WS`   | `/twilio/media`             | Media Stream bidirecional assinado                              |

A interface destinada ao utilizador não possui autenticação multiutilizador nesta v1; não a exponha diretamente a uma rede não fidedigna.

## Desenvolvimento e verificação

```bash
uv sync
uv run ruff check .
uv run pytest
```

Existe uma checklist manual com fornecedores reais em [Testes](docs/TESTING.md). Encontrará detalhes mais aprofundados sobre o design em [Arquitetura](docs/ARCHITECTURE.md).

## Limitações atuais

* Os objetos WebSocket ativos e a orquestração de chamadas em curso são locais ao processo; reiniciar o processo termina uma conversa ativa, embora os dados históricos permaneçam armazenados.
* O inicializador do schema é adequado para esta v1, mas não constitui um sistema geral de migrações.
* A interface foi concebida para um único operador de confiança e não possui autenticação de contas nem camada CSRF.
* Indisponibilidades dos fornecedores e callbacks atrasados de gravação/resumo permanecem visíveis como falhas que podem ser repetidas, em vez de serem ocultadas.
* Não estão implementados deteção de voicemail, novas tentativas automáticas, política de horários permitidos para chamadas nem coordenação distribuída entre múltiplos workers.
