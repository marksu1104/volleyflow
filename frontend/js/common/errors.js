// The API's error messages, said the way a person using the app would say them.

/** Turns the API's own error detail — deliberately English; see
 * the project's language policy — into something a Chinese-speaking
 * organizer or member can actually read.
 *
 * Matched against fixed phrases in the message, not a code the backend
 * doesn't send: giving every one of forty routes a stable error code
 * would be a far larger change than this project's size calls for, and
 * would touch code that a hard-won test suite already covers. What
 * matters in practice is the handful of refusals an ordinary tap can
 * actually reach — a duplicate signup, a season already settled, a
 * roster at capacity — and those are what this covers.
 *
 * Anything not recognised still reaches the reader wrapped in a Chinese
 * sentence, with the original reason attached rather than replacing it —
 * a category this hasn't been taught yet is not the same as no reason at
 * all.
 */
function translateApiError(detail) {
  const text = String(detail == null ? "" : detail).trim();
  for (const [pattern, translate] of _API_ERROR_PATTERNS) {
    const match = text.match(pattern);
    if (match) return translate(match);
  }
  return `操作失敗（${text}）`;
}

const _API_ERROR_PATTERNS = [
  // Identity. By the time one of these is on screen, restarting the
  // session has already been tried and couldn't help, so each says what
  // to do next rather than what went wrong. Before these existed they all
  // arrived as 「操作失敗（…）」 with the English in brackets — which is
  // how creating a club "just failed" on the production site.
  [/^LINE rejected this ID token$/, () => "登入已過期，請關閉頁面後從 LINE 重新開啟"],
  [/^Missing bearer token$/, () => "尚未登入，請從 LINE 開啟這個頁面"],
  [/^No player identified for this LINE account yet$/, () => "找不到你的帳號資料，請重新開啟頁面"],
  [/^Invite links aren't configured on this server$/, () => "邀請連結功能尚未設定，請聯絡開發者"],
  // Membership and permissions.
  [/^Already a member of this (club|season)$/, () => "已經是這裡的成員了"],
  [/^Not a member of this (club|season)$/, () => "不是這裡的成員"],
  [/^Player \d+ is not a member of this season$/, () => "這個人不是本季的成員"],
  [/^Player is not a member of this club$/, () => "這個人不是球隊成員"],
  [/^Player is not a fixed member of this game's season$/, () => "這個人不是本季的固定成員"],
  [/^You are not a member of this club$/, () => "你不是這個球隊的成員"],
  [/^Waiting for the organizer to approve you$/, () => "還在等管理員核准，核准後才能使用"],
  [/^Already asked to join/, () => "已經送出申請，正在等管理員核准"],
  [/^Not waiting to be approved$/, () => "這個人已經不在待核准名單上了"],
  [/^Only a payment or refund can be undone$/, () => "只有收款或退款可以復原"],
  [/^That payment has already been undone$/, () => "這筆已經復原過了"],
  [/^No such ledger entry$/, () => "找不到這筆帳"],
  // Moving a game to another date.
  [/^This season already has a game on that date$/, () => "本季該日已有場次"],
  [/^That date has already been and gone$/, () => "那一天已經過了，請選之後的日期"],
  [/^This game is cancelled, so it cannot be moved$/, () => "本場已取消，無法編輯"],
  [/^Pick two different people to merge$/, () => "請選兩個不同的人合併"],
  [
    /^Only a name typed in by hand can be merged into someone else$/,
    () => "有 LINE 帳號的人不能被合併掉，只能合併手動輸入的名字",
  ],
  [/^Both are down for the same game/, () => "這兩個人在同一場都有紀錄，請先處理那一場再合併"],
  [
    /^(.+?) is no longer in this club$/,
    (m) => `「${m[1]}」已經不在這個球隊了，請直接輸入名字報名`,
  ],
  [/^Only this club's organizer can do that$/, () => "只有管理員可以這麼做"],
  [/^This is the club's only organizer$/, () => "這是球隊唯一的管理員，不能移除"],
  [
    /^Still a fixed member of a season/,
    () => "仍是某一季的固定成員，請先將此人從該季名單移除",
  ],
  [
    /^Still has money to settle with this club/,
    () => "還有未結清的款項，請先收款或退款再移除",
  ],
  [
    /^Still signed up for a game to come/,
    () => "還有尚未進行的報名，請先取消報名再移除",
  ],
  [/^That player isn't on court for that game$/, () => "此人未列於該場出席名單，無法收取臨打費"],
  [
    /^You can only do that for yourself, unless you're the organizer$/,
    () => "只有本人或管理員可以操作",
  ],
  [/^You can only list your own clubs$/, () => "只能查看自己的球隊"],
  [
    /^You can only rename yourself, or someone you added who has no account$/,
    () => "只能改自己的名字，或是你新增、還沒有帳號的人",
  ],
  [/^You can only set your own gender$/, () => "只能設定自己的性別"],
  [
    /^That person has their own account/,
    () => "此人已有 LINE 帳號，需由本人操作",
  ],
  [/^Those are the same player$/, () => "這是同一個人"],

  // Signup, absence, waitlist.
  [
    /^(.+?) is already signed up for this game$/,
    (m) => `${m[1]}已在本場名單上`,
  ],
  [/^That player is already signed up for this game$/, () => "此人已在本場名單上"],
  [
    /^(.+?) is already on the waitlist for this game$/,
    (m) => `${m[1]}已在本場候補名單上`,
  ],
  [
    /^(.+?) is a fixed member of this season — no need to sign up$/,
    (m) => `${m[1]}是本季固定成員，不需要另外報名`,
  ],
  [
    /^(.+?) is a fixed member of this season and is already expected/,
    (m) => `${m[1]}是本季固定成員，本來就會出席，不能再代打別人`,
  ],
  [
    /^This player already has an absence recorded for this game$/,
    () => "此人本場已請假",
  ],
  [/^This absence was cancelled$/, () => "這筆請假已經取消了"],
  [/^Already cancelled$/, () => "已經取消了"],
  [/^Past this season's change deadline for this game$/, () => "已過本場更動期限"],

  // Capacity and the roster.
  //
  // ⚠️ Nothing in the API raises the message below any more: `seasons.py`
  // now says "No room for another member on {date}, and there is no
  // drop-in to move back to the waitlist." (the pattern after this one).
  // Kept rather than deleted because `games.py:255` and two other sites
  // re-raise a ValueError's own text through `str(exc)`, and a wording
  // that isn't in the source today can still arrive from one of those.
  // If you are confident it can't, delete it — but check those three
  // first. Noted 2026-09-25.
  [
    /^No room for another member: (.+) already \d+ on court\. Mark somebody absent or cancel a drop-in on those games first\.$/,
    (m) => `無法加入固定名單：${m[1]}已經額滿。請先將一位固定成員設為請假，或移除該場臨打`,
  ],
  [
    /^No room for another member on (.+), and there is no drop-in to move back to the waitlist\.$/,
    (m) => `無法加入固定名單：${m[1]} 已額滿，且沒有可以移回候補的臨打`,
  ],
  [
    /^(\d+) fixed members is more than the capacity of (\d+)$/,
    (m) => `固定成員 ${m[1]} 人超過人數上限 ${m[2]} 人`,
  ],
  [
    /^Can't lower capacity to (\d+) — (\d+) people are already on the roster or on a game's court$/,
    (m) => `人數上限無法調為 ${m[1]} 人：目前已有 ${m[2]} 人在名單或出席中`,
  ],
  [
    /^This season already has (\d+) fixed members for (\d+) slots/,
    (m) => `本季已經有 ${m[1]} 位固定成員，上限 ${m[2]} 位。請先調高人數上限，或把一位移出名單`,
  ],
  [
    /^This game is full \((\d+)\) and everyone in it was personally arranged/,
    (m) => `本場已滿 ${m[1]} 人，且皆為指定代打，請先取消一位`,
  ],
  [
    /^This game is full \((\d+)\)\. Name who comes out, or raise the capacity first\.$/,
    (m) => `本場已滿 ${m[1]} 人，請先選擇一位換下，或調高人數上限`,
  ],
  [
    /^That roster entry is already linked to a LINE account$/,
    () => "這個名單上的人已經連結過 LINE 帳號了",
  ],
  [/^That player has no LINE account to link$/, () => "這個人沒有 LINE 帳號可以連結"],
  [
    /^That LINE account already has (.+) of its own — remove the duplicate roster entry instead$/,
    (m) => `此 LINE 帳號已有${m[1]}紀錄，請改為移除重複的名單項目`,
  ],

  // Season and settlement.
  [
    /^The game on (\d{4})-(\d{2})-(\d{2}) has been played — record whether the air conditioning ran on the night instead$/,
    (m) => `${Number(m[2])}/${Number(m[3])} 的場次已結束，請於總覽記錄當天實際是否開冷氣`,
  ],
  [
    /^air conditioning and venue changes cost more than the whole venue bill/,
    () => "冷氣與場地差額合計超過場地費總額，請檢查金額",
  ],
  [
    /^Season is already settled — venue cost can't change now$/,
    () => "本季已結算，場地費無法再修改",
  ],
  // Both spellings. seasons.py says "Season is already settled" in most
  // places and "Season already settled" in one (seasons.py:974). The
  // previous pattern tried to cover both with `i?s?` — but with both
  // optionals dropped that asks for *two* spaces, so it matched neither
  // wording of that route and the English reached the screen. Found
  // 2026-09-25 by running every HTTPException in the API through this
  // table, which is not something reading it could have caught.
  [/^Season (is )?already settled$/, () => "本季已結算"],
  [/^Season is not settled$/, () => "本季尚未結算，無需復原"],
  [/^This settlement has already been undone$/, () => "這次結算已經復原過了"],
  [
    /settled — its books can't be deleted$/,
    () => "有一季已經結算，帳本不能刪除",
  ],
  [/^This season has payments recorded/, () => "本季已有收款紀錄，無法刪除"],
  [/^Season has ended$/, () => "本季已結束，無法發送繳費通知"],
  [/^The previous season is not settled$/, () => "上一季結算後可發送繳費通知"],
  [/^Season can be settled from \d{4}-(\d{2})-(\d{2})$/, (m) => `${Number(m[1])}/${Number(m[2])} 起可結算`],
  [/^Game on \d{4}-(\d{2})-(\d{2}) has an open slot$/, (m) => `${Number(m[1])}/${Number(m[2])} 尚有缺額，補齊後可結算`],
  [/^That would make the season's venue cost negative/, () => "這樣會讓場地費變成負的"],

  // Not found.
  [/^No club with id \d+$/, () => "找不到這個球隊"],
  [/^No season with id \d+$/, () => "找不到該季別"],
  [/^No game with id \d+$/, () => "找不到該場次"],
  [
    /^No game on (\d{4}-\d{2}-\d{2}) in this season$/,
    (m) => `本季沒有 ${m[1]} 的場次`,
  ],
  [/^No absence with id \d+$/, () => "找不到這筆請假"],
  [/^No drop-in with id \d+/, () => "找不到這筆報名"],
  [/^No waitlist entry with id \d+$/, () => "找不到這筆候補"],
  [/^No player (with id \d+|named .+ in this club)$/, () => "找不到這個人"],
  [/^This club no longer exists$/, () => "這個球隊已經不存在了"],
  [/^Invite link not recognised$/, () => "這個邀請連結無法辨識"],

  // Identity.
  [
    /^(Missing bearer token|Invalid or expired LINE ID token|No player identified for this LINE account yet)$/,
    () => "請重新用 LINE 登入",
  ],

  // Everything else that's actually reachable from ordinary use.
  [/^Name can't be empty$/, () => "名字不能空白"],
  [/^Game is already cancelled$/, () => "本場已取消"],
  // The developer-only screens. An ordinary member never reaches these,
  // but an untranslated string is an untranslated string — and leaving
  // them out means the audit can never reach zero, which is how a
  // catalogue stops being maintained.
  [
    /^(The developer overview|Reading reports) isn't configured on this server$/,
    () => "這個功能在伺服器上尚未設定",
  ],
  [/^Only the developer can read (this|reports)$/, () => "只有開發者可以看這一頁"],
  [/^Problem reporting isn't configured yet$/, () => "回報功能尚未設定"],
  [/^Nothing to report$/, () => "沒有內容可以回報"],
  [
    /^Couldn't send the report/,
    () => "送出失敗，請直接聯絡管理員",
  ],
  [
    /^(Screenshot is too large|Screenshot isn't valid base64|Screenshot must be)/,
    () => "截圖格式或大小有問題，請重新截圖",
  ],
  [/^No such screenshot$/, () => "找不到這張截圖"],
];
