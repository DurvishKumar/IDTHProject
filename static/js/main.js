document.addEventListener("DOMContentLoaded", () => {
    const voteForm = document.getElementById("voteForm");
    const uppercaseInputs = document.querySelectorAll(".js-uppercase");
    const lowercaseInputs = document.querySelectorAll(".js-lowercase");
    const passwordToggles = document.querySelectorAll(".js-password-toggle");

    if (voteForm) {
        voteForm.addEventListener("submit", (event) => {
            const confirmed = window.confirm("Are you sure you want to submit this vote? You can vote only once.");
            if (!confirmed) {
                event.preventDefault();
            }
        });
    }

    uppercaseInputs.forEach((input) => {
        input.addEventListener("input", () => {
            input.value = input.value.toUpperCase();
        });
    });

    lowercaseInputs.forEach((input) => {
        input.addEventListener("input", () => {
            input.value = input.value.toLowerCase();
        });
    });

    passwordToggles.forEach((button) => {
        button.addEventListener("click", () => {
            const input = button.parentElement.querySelector(".js-password-field");
            if (!input) {
                return;
            }

            const isPassword = input.type === "password";
            input.type = isPassword ? "text" : "password";
            button.textContent = isPassword ? "Hide" : "Show";
        });
    });
});
