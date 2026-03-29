document.addEventListener("DOMContentLoaded", () => {
    const voteForm = document.getElementById("voteForm");
    const uppercaseInputs = document.querySelectorAll(".js-uppercase");
    const lowercaseInputs = document.querySelectorAll(".js-lowercase");
    const passwordToggles = document.querySelectorAll(".js-password-toggle");
    const otpMeta = document.getElementById("otpMeta");
    const resendOtpButton = document.getElementById("resendOtpButton");
    const resendOtpMessage = document.getElementById("resendOtpMessage");
    const otpExpiryMessage = document.getElementById("otpExpiryMessage");

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

    if (otpMeta) {
        let resendWait = Number(otpMeta.dataset.resendWait || 0);
        let expirySeconds = Number(otpMeta.dataset.expirySeconds || 0);

        const updateOtpUi = () => {
            if (resendOtpButton) {
                resendOtpButton.disabled = resendWait > 0;
            }

            if (resendOtpMessage) {
                resendOtpMessage.textContent = resendWait > 0
                    ? `You can resend OTP in ${resendWait} seconds.`
                    : "You can request a new OTP now.";
            }

            if (otpExpiryMessage) {
                otpExpiryMessage.textContent = expirySeconds > 0
                    ? `OTP expires in ${expirySeconds} seconds.`
                    : "OTP expired. Please request a new OTP.";
            }
        };

        updateOtpUi();

        if (resendWait > 0 || expirySeconds > 0) {
            const timer = window.setInterval(() => {
                if (resendWait > 0) {
                    resendWait -= 1;
                }
                if (expirySeconds > 0) {
                    expirySeconds -= 1;
                }
                updateOtpUi();

                if (resendWait <= 0 && expirySeconds <= 0) {
                    window.clearInterval(timer);
                }
            }, 1000);
        }
    }
});
