# GLCtech · Agente de Relatório Semanal de Tráfego Web

Toda **segunda-feira às 08:00 (horário de Brasília)** o agente:

1. Coleta os dados da **semana anterior (segunda a domingo)** no Google Analytics 4 para `glctech.com.br` e `glctechsec.com`, comparando com a semana antes dela.
2. Gera um **PDF com a identidade visual da GLCtech**: destaques automáticos, indicadores, evolução diária, origem do tráfego, páginas mais acessadas e recomendações.
3. Envia o PDF **anexado** para `diretoria@glctech.com.br` via Zoho Mail, com um resumo no corpo do e-mail.
4. Se algo falhar, envia um **alerta** para o endereço em `ALERT_TO`.

```
glc-relatorio-trafego/
├── glc_relatorio_trafego.py   # agente
├── config.env.example         # modelo de configuração
├── requirements.txt
├── assets/logo_glctech.png    # logo oficial
├── credentials/               # chave da service account (não versionar)
├── output/                    # PDFs gerados (mantém os últimos 26)
├── logs/relatorio.log
└── deploy/                    # install.sh + systemd service/timer
```

---

## 1. Acesso ao Google Analytics 4 (service account)

1. Acesse o **Google Cloud Console** e crie (ou selecione) um projeto, por exemplo `glctech-relatorios`.
2. Em **APIs e serviços > Biblioteca**, ative a **Google Analytics Data API**.
3. Em **IAM e administrador > Contas de serviço**, crie uma conta (ex.: `relatorio-ga4`).
4. Na conta criada, abra **Chaves > Adicionar chave > JSON** e baixe o arquivo.
5. Copie o e-mail da service account (`relatorio-ga4@glctech-relatorios.iam.gserviceaccount.com`).
6. No **GA4**, para **cada uma das duas propriedades**, vá em **Administrador > Gerenciamento de acesso à propriedade > + > Adicionar usuários**, cole esse e-mail e dê o papel **Leitor**.

## 2. Senha de aplicativo no Zoho Mail

A autenticação em dois fatores do Zoho bloqueia a senha normal no SMTP. Use uma senha de aplicativo:

1. Acesse **accounts.zoho.com > Segurança > Senhas específicas de aplicativos**.
2. Gere uma senha com o nome `Relatorio GA4`.
3. Cole em `SMTP_PASSWORD` no `config.env`.

> Para enviar como `diretoria@` ou `marketing@` em vez do seu e-mail pessoal, ajuste `MAIL_FROM`. O endereço precisa estar liberado como remetente na sua conta Zoho, e está: esses grupos já aparecem configurados na conta `andre.cezar@glctech.com.br`.

## 3. Instalação no servidor

```bash
cd glc-relatorio-trafego
sudo RUN_USER=andre ./deploy/install.sh

# chave do GA4
sudo cp ~/Downloads/glctech-relatorios-xxxx.json /opt/glc-relatorio-trafego/credentials/service-account.json
sudo chown andre:andre /opt/glc-relatorio-trafego/credentials/service-account.json
sudo chmod 600 /opt/glc-relatorio-trafego/credentials/service-account.json

# senha SMTP
sudo -u andre nano /opt/glc-relatorio-trafego/config.env
```

## 4. Testes

```bash
cd /opt/glc-relatorio-trafego

# a) layout com dados fictícios, sem GA4 e sem e-mail
./venv/bin/python glc_relatorio_trafego.py --mock --no-email

# b) dados reais do GA4, sem e-mail (valida a service account)
./venv/bin/python glc_relatorio_trafego.py --no-email

# c) envio real (sugestão: testar primeiro com MAIL_TO apontando para você)
./venv/bin/python glc_relatorio_trafego.py

# disparar pelo systemd, como o agendamento fará
sudo systemctl start glc-relatorio-trafego.service
journalctl -u glc-relatorio-trafego.service -n 50 --no-pager
```

## 5. Agendamento

O timer do systemd roda **segunda 08:00 America/Sao_Paulo**. Com `Persistent=true`, se o servidor estiver desligado nesse horário, o relatório é enviado assim que ele voltar.

```bash
systemctl list-timers glc-relatorio-trafego.timer   # próxima execução
tail -f /opt/glc-relatorio-trafego/logs/relatorio.log
```

Para mudar o horário, edite `OnCalendar` em `/etc/systemd/system/glc-relatorio-trafego.timer` e rode `sudo systemctl daemon-reload`.

**Alternativa com cron** (se preferir, no lugar do systemd):

```cron
CRON_TZ=America/Sao_Paulo
0 8 * * 1 cd /opt/glc-relatorio-trafego && ./venv/bin/python glc_relatorio_trafego.py >> logs/cron.log 2>&1
```

## Solução de problemas

| Sintoma | Causa provável |
|---|---|
| `403 User does not have sufficient permissions` | Service account não adicionada como Leitor na propriedade GA4 |
| `Google Analytics Data API has not been used` | API não ativada no projeto do Google Cloud |
| `SMTPAuthenticationError 535` | Senha normal no lugar da senha de aplicativo, ou host errado (use `smtppro.zoho.com`) |
| Semana com todos os valores zerados | Tag do GA4 removida do site ou filtro de dados excluindo tudo |
