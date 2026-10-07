# Market Brief ko samajhne ka seedha tareeqa

Tumhara IDE connect karne ki zarurat nahi. Project VS Code mein open kar sakte ho.
App chalane ke liye baad mein ChatGPT Pro required nahi; hosting aur API limits apni jagah hain.

## Button dabane par kya hota hai?

1. Browser mein ticker likhte ho.
2. `static/app.js` ticker aur mode `/api/analyze` ko bhejta hai.
3. `app/main.py` input check karke LangGraph run karta hai.
4. `retrieve` node actual providers se records laata hai; demo synthetic records use karta hai.
5. `calculate` node Python se numbers calculate karta hai.
6. `draft` node Groq ko chhota evidence bundle deta hai aur explanation leta hai.
7. `verify` citation IDs aur format check karta hai; interpretation ki sachai guarantee nahi karta.
8. Backend JSON return karta hai. JavaScript cards aur chart bana deta hai.

Pehle sirf teen jagah parho: `app.js` ka `analyze()`, `main.py` ka `analyze()` aur
`workflow.py` ke chaar nodes. Phir `finance.py` aur `sources.py` par aao.

## Keys

`.env.example` ki copy `.env` banao. Groq aur Alpha Vantage keys lagao.
`SEC_USER_AGENT` mein app ka naam aur contact email likho. App restart karo.
Render par ye values Environment settings mein lagengi. Browser mein keys nahi jaati.

## Demo aur live

Demo numbers invented hain aur Groq call nahi hoti. Live actual records laata hai.
Groq missing ya unavailable ho to "Rule-based brief" clearly likha aata hai.
API fail hone par chupke sample records nahi dikhaye jaate.

Is V1 mein paid news, realtime quotes, login, watchlist ya poori filing ka RAG nahi.
Recent filing ka link milta hai. Yeh scope jaan-boojh kar chhota hai taake tum code samajh sako.
