/* Recorded Duopra sessions.
   Audio, transcripts, tool calls and their timestamps come from the saved
   evaluation runs (Full-Duplex-Bench v3, seed 641). Tools are benchmark mocks.
   Times are on the trimmed recordings in assets/audio: 3.5 s of network transport
   latency is cut from the pause between the two turns of each session, and the
   silence at both ends is trimmed (site-work/build/trim.py). Bar heights:
   assets/peaks.js.
   timing: retimed by site-work/build/retime.py once; do not run it again on this file. */
window.DUOPRA_CASES = [
  {
    id: "correction",
    seed: 641,
    duration: 30.08,
    audio: { user: "assets/audio/correction-user.mp3", assistant: "assets/audio/correction-assistant.mp3" },
    en: {
      index: "01",
      kind: "Self-correction",
      quote: "It's actually 150 euros, not 100.",
      cover: "The user changes the number mid-request.",
      title: "The number changes while the user is still talking.",
      blurb: "A request revised in the middle of being spoken. Only the corrected value reaches the tool.",
      takeaway: "The call carries 150. The abandoned 100 never reaches the tool."
    },
    zh: {
      index: "01",
      kind: "中途改口",
      quote: "其实是 150 欧元，不是 100。",
      cover: "用户在一句话里改掉了金额。",
      title: "话还没说完，数字就改了。",
      blurb: "请求在说的过程中被改写。只有改口后的值进入工具调用。",
      takeaway: "调用带的是 150。被放弃的 100 没有进入工具。"
    },
    user: [
      { t: [0.60, 5.16], en: "Well, I want to calculate how much 100 euros would be in US dollars —", zh: "呃，我想算一下 100 欧元换成美元是多少——" },
      { t: [6.48, 11.26], en: "oh wait, I was looking at the wrong invoice. It's actually 150 euros, not 100.", zh: "哦等等，我看错发票了。其实是 150 欧元，不是 100。" },
      { t: [11.90, 13.84], en: "So 150 euros to US dollars.", zh: "所以是 150 欧元换美元。" }
    ],
    assistant: [
      { t: [16.70, 20.18], en: "I will check the current exchange rate for you.", zh: "我帮你查一下当前汇率。" },
      { t: [22.20, 28.88], en: "One hundred fifty Euros is one hundred sixty five US dollars at the current rate.", zh: "按当前汇率，150 欧元是 165 美元。" }
    ],
    calls: [
      { at: 19.05, fn: "get_exchange_rate",
        args: { amount: 150, from_currency: "EUR", to_currency: "USD" },
        result: { status: "success", converted_amount: 165.0, rate: 1.1 },
        en: "Sent while Duopra is speaking", zh: "在 Duopra 说话的同时发出" }
    ],
    chapters: [
      { at: 0.56, en: "The request", zh: "提出请求" },
      { at: 6.46, en: "The correction", zh: "改口" },
      { at: 16.66, en: "Duopra starts speaking", zh: "Duopra 开口" },
      { at: 18.56, key: true, en: "The call — mid-sentence", zh: "调用发出——话说到一半" },
      { at: 22.16, en: "It reports 165", zh: "报出 165" }
    ]
  },
  {
    id: "two-steps",
    seed: 641,
    duration: 42.38,
    audio: { user: "assets/audio/two-steps-user.mp3", assistant: "assets/audio/two-steps-assistant.mp3" },
    en: {
      index: "02",
      kind: "Two steps",
      quote: "And then, um, I also want to set my credit card payment to come from checking.",
      cover: "Two account changes in one disfluent request.",
      title: "Two tasks in one breath, two calls, two results.",
      blurb: "One hesitant request holding two separate updates. Each result is spoken only after it comes back.",
      takeaway: "The second call goes out at 32.9 s — while the first result is being read aloud."
    },
    zh: {
      index: "02",
      kind: "多步任务",
      quote: "然后，嗯，我还想把信用卡还款设成从支票账户扣。",
      cover: "一句不流利的话里有两个独立操作。",
      title: "一口气说两件事，两次调用，两条结果。",
      blurb: "一句磕磕绊绊的请求里有两个独立操作。每条结果都在返回之后才被念出。",
      takeaway: "第二次调用发生在 32.9 秒——正念着第一条结果的时候。"
    },
    user: [
      { t: [0.60, 7.32], en: "Well… like, first… um, I want to change my mortgage autopay so it pulls from, uh, savings —", zh: "那个…嗯，首先…我想把房贷的自动扣款改成从，呃，储蓄账户扣——" },
      { t: [8.22, 9.54], en: "that's the right account for that.", zh: "那个账户才对。" },
      { t: [10.18, 10.80], en: "And then, um,", zh: "然后，嗯，" },
      { t: [11.52, 16.96], en: "I also want to set my credit card payment to come from checking.", zh: "我还想把信用卡还款设成从支票账户扣。" }
    ],
    assistant: [
      { t: [18.96, 23.22], en: "I will update your autopay settings for both the mortgage and credit card.", zh: "我来帮你更新房贷和信用卡的自动扣款设置。" },
      { t: [29.98, 34.70], en: "Your mortgage autopay is now set to use your savings account.", zh: "房贷自动扣款已经改为使用你的储蓄账户。" },
      { t: [36.20, 41.18], en: "Your credit card autopay has been updated to pull from your checking account.", zh: "信用卡自动扣款已更新为从支票账户扣。" }
    ],
    calls: [
      { at: 26.76, fn: "modify_autopay",
        args: { bill_type: "mortgage", source_account: "savings" },
        result: { status: "success", autopay_enabled: true, bill: "mortgage", source: "savings" },
        en: "First step", zh: "第一步" },
      { at: 32.94, fn: "modify_autopay",
        args: { bill_type: "credit_card", source_account: "checking" },
        result: { status: "success", autopay_enabled: true, bill: "credit_card", source: "checking" },
        en: "Sent while the first result is being read", zh: "念第一条结果的同时发出" }
    ],
    chapters: [
      { at: 0.58, en: "Both requests, one breath", zh: "一口气说出两件事" },
      { at: 18.98, en: "Duopra takes both", zh: "Duopra 接下两件事" },
      { at: 26.58, en: "First call", zh: "第一次调用" },
      { at: 32.68, key: true, en: "Second call — still speaking", zh: "第二次调用——还在说话" },
      { at: 36.18, en: "Second result", zh: "第二条结果" }
    ]
  }
];
