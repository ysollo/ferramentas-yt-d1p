# YTD1P Downloader

Downloader simples para vídeos e áudios do YouTube no Windows, com interface gráfica.

Versão atual: **0.3.1**.

## O que mudou nesta versão

- atualização do yt-dlp para `2026.8.19`;
- seleção de formatos alinhada ao fluxo que usa HLS/m3u8 quando necessário;
- melhor fallback para vídeo e áudio sem descartar formatos WebM;
- validação com vídeo comum, live, Short e extração de áudio.
- verificação de atualizações pela release pública, com fallback seguro quando estiver offline.
- download validado por SHA-256 e atualização assistida com auxiliar separado e rollback.

O projeto usa [yt-dlp](https://github.com/yt-dlp/yt-dlp) como motor e FFmpeg para juntar streams
e converter áudio. A interface foi pensada para uso direto por pessoas que não querem montar
comandos no terminal, mas mantém um painel técnico completo para diagnóstico.

## Recursos atuais

- download de vídeo em modo automático ou com limite de resolução, sem restringir a fonte a MP4;
- extração somente de áudio em MP3, M4A, Opus, FLAC ou WAV;
- seleção de pasta de destino, lembrada localmente;
- progresso, cancelamento e proteção contra downloads duplicados;
- mensagens resumidas para uso normal e detalhes técnicos completos;
- tentativa opcional de compatibilidade com o YouTube usando WebPoClient;
- suporte opcional à sessão local do Chrome, Firefox ou Edge (Chrome é o padrão);
- proteção contra colagem acidental de logs enormes no campo de URL.

A aba de playlists está planejada, mas ainda não faz parte desta versão.

No modo automático, o yt-dlp escolhe a melhor combinação de vídeo e áudio que o link realmente
oferece. Ao escolher uma resolução, o aplicativo tenta essa resolução ou uma menor disponível;
containers como WebM não são descartados apenas por não serem MP4. A escolha explícita dos IDs e
formatos descobertos antes do download continua planejada para uma próxima versão.

O aplicativo verifica a release estável mais recente do GitHub em segundo plano e também oferece a
opção manual em `Ajuda > Verificar atualizações`. A instalação ainda é assistida: o programa abre a
página oficial para o usuário baixar o ZIP, valida o arquivo e pode solicitar a permissão do Windows
para substituir a pasta da instalação.

## Usar a versão Windows

Baixe o ZIP na página de Releases, extraia a pasta inteira e execute `YTD1P.exe`. Não remova a
pasta `_internal`: ela contém os runtimes necessários para o aplicativo funcionar.

## Executar pelo código-fonte

Requer Python 3.11+ e as dependências listadas em `requirements.txt`:

```powershell
python -m pip install -r requirements.txt
python -m src.app
```

Para baixar e converter mídia pelo código-fonte, FFmpeg e FFprobe precisam estar disponíveis no
`PATH`. O runtime Deno é necessário para resolver alguns desafios JavaScript do YouTube. O
provedor WebPoClient é instalado junto com as dependências e empacotado na distribuição Windows.

## Gerar a distribuição Windows

Com PyInstaller, FFmpeg, FFprobe e Deno disponíveis no `PATH`:

```powershell
powershell -ExecutionPolicy Bypass -File .\build\build_windows.ps1 -Clean
```

A distribuição em modo pasta será criada em `dist\YTD1P\`. Para entregar a terceiros, compacte a
pasta inteira mantendo sua estrutura.

## Privacidade e limitações

O aplicativo não envia cookies, tokens, URLs ou logs para um servidor. A preferência da última
pasta é salva localmente. Vídeos privados, restritos, indisponíveis ou bloqueados pelo YouTube
podem exigir login, sessão do navegador ou simplesmente não ser baixáveis.

O modo de compatibilidade WebPoClient usa um navegador Chromium/Chrome instalado no computador
para gerar tokens temporários. Isso não compartilha a sessão do usuário por padrão e não exige
login; se o navegador auxiliar não estiver disponível, o fluxo normal ainda pode ser usado.

Use a ferramenta somente para conteúdo que você tem autorização para baixar e respeite os termos
dos sites e os direitos dos criadores.

## Licença

Este projeto é distribuído sob a licença MIT. Consulte `LICENSE`.

<!-- A documentação histórica de desenvolvimento fica fora do repositório público. -->

## Estrutura pública

- `src/` — código-fonte do downloader;
- `build/` — scripts e instruções para gerar o executável;
- `dist/` — versões distribuíveis geradas localmente e ignoradas pelo Git;
- `docs/` — instruções públicas selecionadas.
