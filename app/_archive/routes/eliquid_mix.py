{% extends "base.html" %}
{% block title %}E-Liquid Mixing | {{ recipe.name }}{% endblock %}

{% block content %}
<style>
    .step-active { border: 2px solid var(--primary-orange); background: #1a1a1a; }
    .step-complete { opacity: 0.5; border-left: 5px solid var(--success); }
    .scale-readout { font-family: 'Courier New', monospace; font-size: 5rem; font-weight: bold; color: var(--primary-orange); text-align: center; padding: 20px; background: #000; border-radius: 8px; border: 2px solid #222; margin-bottom: 30px; }
    .overpour-halt { position: fixed; top: 0; left: 0; width: 100%; height: 100%; background: rgba(139, 0, 0, 0.98); z-index: 15000; display: flex; flex-direction: column; justify-content: center; align-items: center; color: #fff; text-align: center; padding: 40px; backdrop-filter: blur(10px); }
</style>

<div class="container">
    <div class="section-card">
        <div style="display: flex; justify-content: space-between; align-items: center;">
            <h1 style="margin:0;">{{ recipe.name }}</h1>
            <span class="badge {% if has_nicotine %}badge-experimental{% else %}badge-stable{% endif %}">
                {% if has_nicotine %}☢ NICOTINE SESSION{% else %}STANDARD MIX{% endif %}
            </span>
        </div>
        <p style="color: var(--text-muted);">Recipe Code: {{ recipe.recipe_code }} | Target Volume: {{ target_ml }}ml</p>
    </div>

    <div class="scale-readout" id="scale-display">0.00g</div>

    <div id="steps-container"></div>
</div>

{% if needs_supervisor %}
<div id="supervisor-lock" style="position: fixed; top: 0; left: 0; width: 100%; height: 100%; background: rgba(0,0,0,0.98); z-index: 12000; display: flex; justify-content: center; align-items: center;">
    <div class="section-card" style="width: 450px; text-align: center; border: 1px solid var(--primary-orange);">
        <h2 style="color: var(--primary-orange);">Supervision Required</h2>
        <p>Nicotine detected. A <strong>Ram or higher</strong> must authorize this batch.</p>
        <div style="margin-top: 20px;">
            <input type="text" id="sup-user" placeholder="Username" style="width: 100%; padding: 12px; margin-bottom: 10px; background: #000; border: 1px solid #333; color: #fff;">
            <input type="password" id="sup-pass" placeholder="Password/PIN" style="width: 100%; padding: 12px; margin-bottom: 20px; background: #000; border: 1px solid #333; color: #fff;">
            <button onclick="authorizeStart()" class="btn-primary" style="width: 100%;">Authorize Session</button>
        </div>
    </div>
</div>
{% endif %}

<div id="nicotine-halt" class="overpour-halt" style="display: none;">
    <h1 style="font-size: 3rem; color: #ff4444;">☢ NICOTINE OVERPOUR ☢</h1>
    <div style="background: #111; padding: 30px; border-radius: 12px; border: 1px solid #ff4444; max-width: 500px;">
        <p style="font-size: 1.2rem;">Target was: <strong id="halt-target">0</strong>g</p>
        <p>The scale reading is frozen. <strong>DO NOT TARE.</strong></p>
        <p style="color: #bbb; font-size: 0.9rem;">Supervisor: Remove excess nicotine from the bottle manually until the scale matches the target. Then authorize resume.</p>
        <hr style="border: 0; border-top: 1px solid #333; margin: 20px 0;">
        <input type="password" id="halt-sup-pass" placeholder="Supervisor PIN" style="width: 100%; padding: 12px; margin-bottom: 15px; background: #000; color: #fff; border: 1px solid #444;">
        <button onclick="resumeAfterIntervention()" class="btn-primary" style="width: 100%;">Authorize Resume</button>
    </div>
</div>

<script>
    const ingredients = {{ ingredients_json | safe }};
    let currentStepIndex = 0;
    let currentStepWeight = 0; // The "Tared" weight for the current step
    let supervisorId = null;

    function renderSteps() {
        const container = document.getElementById('steps-container');
        container.innerHTML = ingredients.map((ing, index) => `
            <div id="step-${index}" class="section-card ${index === currentStepIndex ? 'step-active' : ''} ${index < currentStepIndex ? 'step-complete' : ''}">
                <div style="display: flex; justify-content: space-between; align-items: center;">
                    <span>
                        <strong style="font-size: 1.2rem;">${index + 1}. ${ing.brand} ${ing.name}</strong>
                        ${ing.is_nicotine ? '<span class="badge badge-experimental" style="margin-left: 10px;">NICOTINE</span>' : ''}
                    </span>
                    <span style="font-size: 1.5rem;">Target: <strong>${ing.target_g}g</strong></span>
                </div>
            </div>
        `).join('');
    }

    function checkWeight() {
        const currentStep = ingredients[currentStepIndex];
        const display = document.getElementById('scale-display');
        display.innerText = currentStepWeight.toFixed(2) + "g";

        // OVERPOUR LOGIC (Tolerance 0.05g)
        if (currentStepWeight > currentStep.target_g + 0.05) {
            if (currentStep.is_nicotine) {
                document.getElementById('halt-target').innerText = currentStep.target_g;
                document.getElementById('nicotine-halt').style.display = 'flex';
                return; // Stop processing further weight
            } else {
                display.style.color = "#ff4444";
            }
        } else {
            display.style.color = "var(--primary-orange)";
        }

        // AUTO-TARE & NEXT (±0.01g precision)
        if (Math.abs(currentStepWeight - currentStep.target_g) <= 0.01) {
            moveToNextStep();
        }
    }

    function authorizeStart() {
        const user = document.getElementById('sup-user').value;
        const pass = document.getElementById('sup-pass').value;

        fetch("{{ url_for('eliquid_mix.authorize_supervisor') }}", {
            method: "POST",
            headers: {"Content-Type": "application/json"},
            body: JSON.stringify({username: user, password: pass})
        })
        .then(res => res.json())
        .then(data => {
            if(data.status === 'success') {
                supervisorId = data.supervisor_id;
                document.getElementById('supervisor-lock').style.display = 'none';
                renderSteps();
            } else { alert(data.message); }
        });
    }

    function resumeAfterIntervention() {
        const pin = document.getElementById('halt-sup-pass').value;
        // Re-use auth for simplicity
        fetch("{{ url_for('eliquid_mix.authorize_supervisor') }}", {
            method: "POST",
            headers: {"Content-Type": "application/json"},
            body: JSON.stringify({username: "OVERRIDE", password: pin})
        })
        .then(res => res.json())
        .then(data => {
            if(data.status === 'success') {
                document.getElementById('nicotine-halt').style.display = 'none';
                document.getElementById('halt-sup-pass').value = '';
                // Note: We DO NOT reset currentStepWeight here.
            } else { alert("Unauthorized Supervisor."); }
        });
    }

    function moveToNextStep() {
        if (currentStepIndex < ingredients.length - 1) {
            currentStepIndex++;
            currentStepWeight = 0; // THE TARE
            document.getElementById('scale-display').innerText = "0.00g";
            renderSteps();
        } else {
            finalizeMix();
        }
    }

    function finalizeMix() {
        const payload = {
            recipe_id: {{ recipe.id }},
            version_id: {{ version.id }},
            target_ml: {{ target_ml }},
            supervisor_id: supervisorId,
            ingredients: ingredients.map(ing => ({
                ingredient_id: ing.ingredient_id,
                poured_g: ing.target_g 
            }))
        };

        fetch("{{ url_for('eliquid_mix.finalize_eliquid') }}", {
            method: "POST",
            headers: {"Content-Type": "application/json"},
            body: JSON.stringify(payload)
        })
        .then(res => res.json())
        .then(data => {
            if(data.status === 'success') {
                alert("Batch Complete. Inventory updated.");
                window.location.href = "{{ url_for('recipes.library') }}";
            }
        });
    }

    // SIMULATED SCALE (Press 'w' to add, 's' to subtract)
    window.addEventListener('keypress', (e) => {
        if(e.key === 'w') { currentStepWeight += 0.01; checkWeight(); }
        if(e.key === 's') { currentStepWeight -= 0.01; checkWeight(); }
    });

    window.onload = () => { if(!{{ needs_supervisor | tojson }}) renderSteps(); };
</script>
{% endblock %}