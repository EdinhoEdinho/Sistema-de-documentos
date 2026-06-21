# Sistema-de-documentos

Sistema Flask para cadastro e acompanhamento de documentos, empresas, colaboradores, NRs, ASOs e alertas de vencimento.

## Configuracao

Antes de executar, configure as variaveis de ambiente do banco:

```powershell
$env:DB_HOST="seu-host"
$env:DB_USER="seu-usuario"
$env:DB_PASSWORD="sua-senha"
$env:DB_NAME="seu-banco"
$env:DB_PORT="3306"
```

## Execucao

```powershell
python app.py
```
