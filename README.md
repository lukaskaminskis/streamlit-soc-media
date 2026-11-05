# X bookmark viewer

Streamlit page that signs you in with OAuth 2.0 PKCE and, when you choose **Load bookmarks**, shows one page of bookmarks from that X account. It keeps the longest post text that is not only a link, replaces `t.co` links with `expanded_url`, and always shows the post source URL `https://x.com/i/status/{id}`. If the text is only a link, the page says the article text is unavailable and leaves an empty box for you to paste it. That box is never filled from the URL.

The access token stays in Streamlit session state for the browser session. It is not written to disk. No client secret is used or stored.

## Environment

| Name | Purpose |
| --- | --- |
| `X_CLIENT_ID` | OAuth 2.0 client id of your X app |
| `X_REDIRECT_URI` | Callback URL registered on that app. For this page it is the app address, for example `http://127.0.0.1:8501/` |
| `GEMINI_API_KEY` | Gemini API key. Environment wins; otherwise `.streamlit/secrets.toml`. The page never shows the value. |
| `GEMINI_MODEL` | Optional model override. When unset, the app uses `model` in `gemini_config.toml`. |

`.streamlit/secrets.toml` is gitignored. Do not commit it, any `.env` file, tokens, or saved writing examples. `.streamlit/secrets.toml.example` lists the setting names with empty values.

## Launch

Put `X_CLIENT_ID` in `.streamlit/secrets.toml`. `X_REDIRECT_URI` is already `http://127.0.0.1:8501/`. If either name is already set in the environment, that value is used and the file is left as-is. No client secret is required.

From this folder:

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
streamlit run app.py --server.address 127.0.0.1 --server.port 8501
```

Open the redirect URL, choose **Sign in with X**, then **Continue to X**, and approve the request. Scopes requested: `bookmark.read`, `tweet.read`, and `users.read`. After sign-in, choose **Load bookmarks**. A rerun does not fetch again.

## Deploy on Railway

This repository is the service. Leave Railway's **Root Directory** empty so the build uses the repository root. Do not set a `/streamlit_soc_media` prefix. Leave the custom start command empty. `railway.toml` at the repo root selects `Dockerfile`. That image installs `requirements.txt` and runs `streamlit run app.py` on `$PORT`, address `0.0.0.0`, headless, with usage stats off. The entry point is `app.py`.

Required variables (names only; set them in Railway, not in Git):

| Name | What to enter |
| --- | --- |
| `X_CLIENT_ID` | Client id of the X app |
| `X_REDIRECT_URI` | Public Railway URL with a trailing slash, for example `https://your-service.up.railway.app/` |
| `GEMINI_API_KEY` | Gemini API key |

`X_REDIRECT_URI` is sent to X exactly as entered. Register that same URL, including the trailing slash, as the callback on the X app. `http://127.0.0.1:8501/` is only the local example. Optional: `GEMINI_MODEL`. When it is unset, the app uses `model` in `gemini_config.toml`. Railway sets `PORT`. Do not commit `secrets.toml`, `.env`, or private writing examples.

**Generate LinkedIn draft** asks Gemini for a fresh post. It does not post to LinkedIn. Facts come from the bookmark text, or from source text you paste when the bookmark is only a link. That box is not filled from the URL. **My writing examples** is for tone, paragraph length, and structure only, and it stays in the browser session across reruns. **Opinion notes** are optional. Leave them blank and the prompt tells Gemini not to invent your beliefs, experiences, or endorsements. The default model is `gemini-3.8-flash` in `gemini_config.toml`, from the Gemini generateContent guide. Set `GEMINI_MODEL` to use a different model without editing code. `GEMINI_API_KEY` stays in the environment or `.streamlit/secrets.toml` and is never shown.

## API details are unverified against X's own docs

Fetches of `https://docs.x.com/x-api/users/get-bookmarks`, the older developer.x.com bookmarks reference, and `https://api.x.com/2/openapi.json` returned 403. This app follows a public mirror of an OpenAPI document titled X API v2, version 2.157:

- https://generaltranslation.mintlify.app/x-api/users/get-bookmarks
- https://generaltranslation.mintlify.app/x-api/posts/bookmarks/quickstart/bookmarks-lookup

From that mirror, not confirmed on the official host:

- Authorize in the browser at `https://x.com/i/oauth2/authorize` with PKCE `S256`. `https://api.x.com/2/oauth2/authorize` returns error 215. Official docs pages returned 403 from this environment, so this host is taken from X's published PKCE flow and a reproduction of that 215 response.
- Exchange the code at `https://api.x.com/2/oauth2/token` with `client_id` in the body and no client secret
- `GET https://api.x.com/2/users/me`
- `GET https://api.x.com/2/users/{id}/bookmarks?max_results=10&tweet.fields=text,note_tweet,entities,article&expansions=article.cover_media,article.media_entities`
- `{id}` must be the authenticated user

Fields requested, from that mirror, and unverified against X's own docs:

- `text` — default post text. A link-only post is often only a `t.co` URL.
- `note_tweet.text` — documented full text for a long post. `note_tweet.entities.urls` carries the same URL objects.
- `entities.urls.expanded_url` — documented replacement for the `t.co` `url`. `unwound_url` is used only when `expanded_url` is absent. The post source URL stays `https://x.com/i/status/{id}`.
- `article` — the data dictionary says this object contains the article text and entities. The OpenAPI mirror lists the field and the expansions `article.cover_media` and `article.media_entities`, and it does not name an article body property. This app reads `article.text` when that property is present. That property name is unverified. `plain_text` is not documented in the mirror, so it is not read. Media from the article expansions is not shown as the article text.

Access tier and billing were not on the pages that loaded, so those requirements are unverified too.
