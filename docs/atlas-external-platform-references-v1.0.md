**A T L A S O P T I M I S A T I O N**

**Atlas External Platform References**

*Shared appendix of external provider and platform documentation cited
by the Atlas Methodology and Atlas Operating System.*

<table>
<colgroup>
<col style="width: 100%" />
</colgroup>
<thead>
<tr class="header">
<th><p><strong>STATUS NOTE</strong></p>
<p>This file is the single source of truth for external platform
references cited by the Atlas Methodology and Atlas Operating System
documents. Both documents point here rather than duplicating this
appendix, so a single update keeps every document current. These
references were verified on 25 August 2026 and must be rechecked before
any methodology version is frozen or a provider adapter is materially
changed.</p></th>
</tr>
</thead>
<tbody>
</tbody>
</table>

# **Referenced by**

• Atlas Methodology v1.0 - Appendix A

• Atlas Operating System v1.0 - Appendix A

# **Platform references**

[**<u>OpenAI Publishers and Developers
FAQ</u>**](https://help.openai.com/en/articles/12627856-publishers-and-developers-faq) -
OAI-SearchBot supports ChatGPT search discovery; GPTBot is a training
control.

[**<u>OpenAI API developer
quickstart</u>**](https://platform.openai.com/docs/quickstart) -
Responses API supports the web_search tool.

[**<u>Google common
crawlers</u>**](https://developers.google.com/crawling/docs/crawlers-fetchers/google-common-crawlers) -
Google-Extended is distinct from Google Search inclusion and ranking.

[**<u>Gemini grounding with Google
Search</u>**](https://ai.google.dev/gemini-api/docs/google-search) -
Current Gemini web grounding behaviour and source annotations.

[**<u>Gemini grounding with Google
Maps</u>**](https://ai.google.dev/gemini-api/docs/maps-grounding) -
Location-aware Maps grounding, currently kept outside AVS.

[**<u>Gemini API
keys</u>**](https://ai.google.dev/gemini-api/docs/api-key) - Standard
keys are being replaced by authorization keys; standard keys are
rejected from September 2026.

[**<u>Perplexity API
quickstart</u>**](https://docs.perplexity.ai/docs/getting-started/quickstart) -
Sonar is the web-grounded response API; Search API returns raw search
results.

[**<u>Perplexity
crawlers</u>**](https://docs.perplexity.ai/docs/resources/perplexity-crawlers) -
PerplexityBot is used to surface and link sites in Perplexity search.

[**<u>Claude web search
tool</u>**](https://platform.claude.com/docs/en/agents-and-tools/tool-use/web-search-tool) -
Server-side web search returns cited results.

[**<u>Anthropic crawler
controls</u>**](https://support.claude.com/en/articles/8896518-does-anthropic-crawl-data-from-the-web-and-how-can-site-owners-block-the-crawler) -
ClaudeBot, Claude-User and Claude-SearchBot have distinct purposes.

[**<u>Google Business Profile API
prerequisites</u>**](https://developers.google.com/my-business/content/prereqs) -
API access requires approval and a verified active profile managed for
60+ days.

[**<u>GitHub scheduled workflow
behaviour</u>**](https://docs.github.com/en/actions/reference/workflows-and-actions/events-that-trigger-workflows) -
Scheduled runs can be delayed or dropped under high load, so Atlas runs
are resumable and reconciled from database state.

**Change control**

Update this file directly when a provider changes documented behaviour,
deprecates an API, or changes auth/tooling requirements. No edit here
changes an Atlas score or movement verdict by itself - see Atlas
Methodology §10 for what does.
