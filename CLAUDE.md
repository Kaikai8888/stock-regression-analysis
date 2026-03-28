# 專案規範

## 網址安全檢查規則

在使用任何工具（WebFetch、瀏覽器、curl 等）存取外部網址之前，必須先執行以下流程：

1. **先至 [Norton Safe Web](https://safeweb.norton.com/) 查詢該網址的安全性**
2. 若 Norton Safe Web 顯示該網址有問題（警告、惡意、詐騙等），**直接阻擋，不得繼續存取**
3. 若 Norton Safe Web 顯示安全，再向使用者請求工具使用權限
4. **請求權限時，必須在訊息中說明「已透過 Norton Safe Web 確認該網址安全」**，並附上被查詢的網址

### 範例說明訊息格式

> 已透過 Norton Safe Web (https://safeweb.norton.com/) 確認以下網址安全，請求存取權限：
> `https://www.example.com/page`
