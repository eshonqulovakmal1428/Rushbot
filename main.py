<!DOCTYPE html>
<html lang="uz">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0, maximum-scale=1.0, user-scalable=no">
    <title>Matematik Test Javoblari</title>
    
    <!-- Telegram Web App API -->
    <script src="https://telegram.org/js/telegram-web-app.js"></script>
    
    <!-- MathLive kutubxonasi (Matematik klaviatura uchun) -->
    <script defer src="https://unpkg.com/mathlive"></script>

    <style>
        :root {
            --bg-color: #f3f4f6;
            --card-bg: #ffffff;
            --text-color: #1f2937;
            --primary-color: #3b82f6;
            --border-color: #e5e7eb;
            --error-color: #ef4444;
        }

        body {
            font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, Helvetica, Arial, sans-serif;
            background-color: var(--bg-color);
            color: var(--text-color);
            margin: 0;
            padding: 15px;
            padding-bottom: 80px; 
        }

        .header {
            font-size: 20px;
            font-weight: bold;
            text-align: center;
            margin-bottom: 20px;
            color: var(--text-color);
        }

        /* --- KIRISH OYNASI DIZAYNI --- */
        #login-screen {
            display: flex;
            flex-direction: column;
            gap: 15px;
            margin-top: 30px;
        }

        .input-field {
            padding: 14px;
            border: 2px solid var(--border-color);
            border-radius: 10px;
            font-size: 16px;
            width: 100%;
            box-sizing: border-box;
            background-color: var(--card-bg);
            text-align: center;
            transition: border-color 0.2s ease;
        }

        .input-field:focus {
            border-color: var(--primary-color);
            outline: none;
        }

        .btn-primary {
            background-color: var(--primary-color);
            color: white;
            padding: 14px;
            border: none;
            border-radius: 10px;
            font-size: 16px;
            font-weight: bold;
            cursor: pointer;
            transition: background-color 0.2s ease;
        }

        .btn-primary:active {
            background-color: #2563eb;
        }

        /* --- TEST OYNASI DIZAYNI --- */
        #test-screen {
            display: none; 
        }

        .section-title {
            font-size: 18px;
            font-weight: bold;
            color: var(--primary-color);
            margin-top: 25px;
            margin-bottom: 10px;
            border-bottom: 2px solid var(--primary-color);
            padding-bottom: 5px;
        }

        .questions-container {
            display: flex;
            flex-direction: column;
            gap: 15px;
        }

        .question-card {
            background-color: var(--card-bg);
            border-radius: 12px;
            padding: 15px;
            box-shadow: 0 2px 4px rgba(0, 0, 0, 0.05);
            border: 1px solid var(--border-color);
        }

        .question-header {
            font-size: 16px;
            font-weight: 600;
            color: var(--text-color);
        }

        /* Yopiq test dizayni */
        .options-grid {
            display: grid;
            grid-template-columns: repeat(4, 1fr);
            gap: 10px;
            margin-top: 10px;
        }

        .radio-label {
            position: relative;
            cursor: pointer;
        }

        .radio-label input {
            position: absolute;
            opacity: 0;
            width: 0;
            height: 0;
        }

        .radio-label span {
            display: flex;
            justify-content: center;
            align-items: center;
            padding: 10px 0;
            background-color: #f9fafb;
            border: 1px solid var(--border-color);
            border-radius: 8px;
            font-weight: bold;
            color: #4b5563;
            transition: all 0.3s ease;
        }

        .radio-label input:checked + span {
            background-color: var(--primary-color);
            color: white;
            border-color: var(--primary-color);
            transform: scale(1.05);
            box-shadow: 0 4px 6px rgba(59, 130, 246, 0.3);
        }

        .options-grid.answered .radio-label input:not(:checked) + span {
            opacity: 0.3;
            background-color: #f3f4f6;
            border-color: #e5e7eb;
        }

        /* Ochiq test dizayni */
        .math-row {
            display: flex;
            align-items: center;
            gap: 10px;
            margin-top: 12px;
        }

        .math-row > span {
            font-weight: bold;
            color: #4b5563;
            width: 20px;
        }

        math-field {
            font-size: 18px;
            flex: 1;
            padding: 10px;
            border: 1px solid var(--border-color);
            border-radius: 8px;
            background-color: #f9fafb;
            box-sizing: border-box;
            outline: none;
            transition: all 0.2s ease;
        }

        math-field:focus-within {
            border-color: var(--primary-color);
            box-shadow: 0 0 0 2px rgba(59, 130, 246, 0.2);
            background-color: #ffffff;
        }

        .unanswered {
            border: 2px solid var(--error-color) !important;
            background-color: #fef2f2 !important;
        }

        /* Klaviaturani telefonda katta va qulay ko'rsatish uchun qo'shimcha CSS */
        math-virtual-keyboard {
            --keycap-height: 50px;
            --keycap-font-size: 20px;
            --keyboard-background: #eef2f6;
        }
    </style>
</head>
<body>

    <!-- KIRISH OYNASI -->
    <div id="login-screen">
        <div class="header">Tizimga kirish</div>
        <!-- Faqat test kodi so'raladi -->
        <input type="text" id="test-code" class="input-field" placeholder="Test kodini kiriting" autocomplete="off">
        <button id="start-btn" class="btn-primary">Davom etish</button>
    </div>

    <!-- TEST OYNASI -->
    <div id="test-screen">
        <div class="header">Test Javoblari</div>

        <div id="mcq-section">
            <div class="section-title">Yopiq testlar (Variantli)</div>
            <div class="questions-container" id="mcq-list"></div>
        </div>

        <div id="math-section">
            <div class="section-title">Ochiq testlar</div>
            <div class="questions-container" id="math-list"></div>
        </div>
    </div>

    <script>
        const tg = window.Telegram.WebApp;
        tg.expand(); 

        const urlParams = new URLSearchParams(window.location.search);
        const expectedCode = urlParams.get('code'); 
        
        const mcqCount = parseInt(urlParams.get('mcq')) || 35;
        const openCount = parseInt(urlParams.get('open')) || 10;
        
        let userCode = "";

        // MathLive klaviaturasini sozlash (6 ta bo'lim, yirik tugmalar bilan)
        window.addEventListener('DOMContentLoaded', () => {
            window.mathVirtualKeyboard.layouts = [
                // 1-bo'lim: Rasmdagi asosiy klaviatura (5 ustunli yirik format)
                {
                    label: '123',
                    rows: [
                        [ "7", "8", "9", "+", "-" ],
                        [ "4", "5", "6", "\\times", "\\div" ],
                        [ "1", "2", "3", { latex: '\\frac{#?}{#?}', label: 'a/b' }, { latex: '\\sqrt{#?}', label: '√x' } ],
                        [ "0", ".", "=", "\\%", "," ],
                        [ { latex: '#?^2', label: 'x²' }, { latex: '#?^{#?}', label: 'xⁿ' }, { latex: '#?_{#?}', label: 'x_j' }, ":", ";" ],
                        [ "[left]", "[right]", "[backspace]", { label: 'Tozalash', command: 'deleteAll', w: 2 } ]
                    ]
                },
                // 2-bo'lim: Asosiy ekraniga sig'magan oldingi belgilar (qavslar va ildizlar)
                {
                    label: '()[]',
                    rows: [
                        [ "\\sqrt[#?]{#?}", "(", ")", "[", "]" ],
                        [ "\\{", "\\}", "\\|", "\\infty", "\\pm" ]
                    ]
                },
                // 3-bo'lim: Funksiyalar
                {
                    label: 'log/sin',
                    rows: [
                        [ { latex: '\\log_{#?}(#?)', label: 'log' }, "\\ln", "\\sin" ],
                        [ "\\cos", "\\tan", "\\cot" ],
                        [ "\\lim_{#?\\to#?}", "\\int", "\\sum" ]
                    ]
                },
                // 4-bo'lim: Tengsizliklar va munosabatlar
                {
                    label: '≠<>',
                    rows: [
                        [ "<", ">", "\\le", "\\ge" ],
                        [ "\\neq", "\\approx", "\\equiv" ],
                        [ "\\in", "\\notin", "\\subset" ]
                    ]
                },
                // 5-bo'lim: O'zgaruvchilar (Harflar)
                {
                    label: 'xyz',
                    rows: [
                        [ "x", "y", "z" ],
                        [ "a", "b", "c" ],
                        [ "n", "k", "m" ]
                    ]
                },
                // 6-bo'lim: Yunon harflari va maxsus belgilar
                {
                    label: 'αβγ',
                    rows: [
                        [ "\\pi", "\\alpha", "\\beta" ],
                        [ "\\gamma", "\\theta", "\\Delta" ],
                        [ "\\infty", "\\emptyset", "\\pm" ]
                    ]
                }
            ];
        });

        // "Davom etish" tugmasi
        document.getElementById('start-btn').addEventListener('click', () => {
            userCode = document.getElementById('test-code').value.trim();

            if (!userCode) {
                tg.showAlert("⚠️ Iltimos, test kodini kiriting!");
                return;
            }

            if (expectedCode && userCode.toUpperCase() !== expectedCode.toUpperCase()) {
                tg.showAlert("❌ Kiritilgan test kodi noto'g'ri!");
                return;
            }

            // Test oynasini ochish
            document.getElementById('login-screen').style.display = 'none';
            document.getElementById('test-screen').style.display = 'block';

            generateQuestions();
            
            const totalFields = mcqCount + (openCount * 2);
            tg.MainButton.text = `Javoblarni yuborish (${totalFields} ta)`;
            tg.MainButton.color = "#3b82f6";
            tg.MainButton.textColor = "#ffffff";
            tg.MainButton.show();
        });

        window.markSelected = function(id) {
            document.getElementById(`grid_${id}`).classList.add('answered');
        };

        function generateQuestions() {
            const mcqList = document.getElementById('mcq-list');
            const mathList = document.getElementById('math-list');

            if (mcqCount === 0) document.getElementById('mcq-section').style.display = 'none';
            if (openCount === 0) document.getElementById('math-section').style.display = 'none';

            // Yopiq testlar
            for (let i = 1; i <= mcqCount; i++) {
                const card = document.createElement('div');
                card.className = 'question-card';
                card.id = `card_${i}`;
                card.innerHTML = `
                    <div class="question-header">${i}-savol:</div>
                    <div class="options-grid" id="grid_${i}">
                        <label class="radio-label"><input type="radio" name="ans_${i}" value="a" onchange="markSelected(${i})"><span>A</span></label>
                        <label class="radio-label"><input type="radio" name="ans_${i}" value="b" onchange="markSelected(${i})"><span>B</span></label>
                        <label class="radio-label"><input type="radio" name="ans_${i}" value="c" onchange="markSelected(${i})"><span>C</span></label>
                        <label class="radio-label"><input type="radio" name="ans_${i}" value="d" onchange="markSelected(${i})"><span>D</span></label>
                    </div>
                `;
                mcqList.appendChild(card);
            }

            // Ochiq testlar
            let mathStart = mcqCount + 1;
            let mathEnd = mcqCount + openCount;
            for (let i = mathStart; i <= mathEnd; i++) {
                const card = document.createElement('div');
                card.className = 'question-card';
                card.id = `card_${i}`;
                card.innerHTML = `
                    <div class="question-header">${i}-savol:</div>
                    <div class="math-row">
                        <span>a)</span> 
                        <math-field id="ans_${i}_a" virtual-keyboard-mode="manual"></math-field>
                    </div>
                    <div class="math-row">
                        <span>b)</span> 
                        <math-field id="ans_${i}_b" virtual-keyboard-mode="manual"></math-field>
                    </div>
                `;
                mathList.appendChild(card);
            }
        }

        function collectAndValidate() {
            let answers = [];
            let isComplete = true;

            document.querySelectorAll('.unanswered').forEach(el => el.classList.remove('unanswered'));

            for (let i = 1; i <= mcqCount; i++) {
                const selected = document.querySelector(`input[name="ans_${i}"]:checked`);
                if (selected) {
                    answers.push(selected.value);
                } else {
                    document.getElementById(`card_${i}`).classList.add('unanswered');
                    isComplete = false;
                }
            }

            let mathStart = mcqCount + 1;
            let mathEnd = mcqCount + openCount;
            for (let i = mathStart; i <= mathEnd; i++) {
                const mathA = document.getElementById(`ans_${i}_a`);
                const mathB = document.getElementById(`ans_${i}_b`);
                
                const valA = mathA.value.trim();
                const valB = mathB.value.trim();

                if (valA !== "") {
                    answers.push(valA);
                } else {
                    mathA.classList.add('unanswered');
                    isComplete = false;
                }

                if (valB !== "") {
                    answers.push(valB);
                } else {
                    mathB.classList.add('unanswered');
                    isComplete = false;
                }
            }

            return { isComplete, answers };
        }

        tg.onEvent('mainButtonClicked', function() {
            const result = collectAndValidate();
            const totalFields = mcqCount + (openCount * 2);
            
            if (!result.isComplete || result.answers.length !== totalFields) {
                tg.showAlert(`⚠️ Iltimos, barcha ${totalFields} ta javobni to'ldiring! (Qizargan maydonlarni tekshiring)`);
                return; 
            }

            const dataToBot = {
                type: "rush_test",
                code: userCode,
                answers: result.answers 
            };
            
            tg.sendData(JSON.stringify(dataToBot));
            tg.close();
        });

    </script>
</body>
</html>
