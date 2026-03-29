document.addEventListener("DOMContentLoaded", () => {
    const voteForm = document.getElementById("voteForm");

    if (voteForm) {
        voteForm.addEventListener("submit", (event) => {
            const confirmed = window.confirm("Are you sure you want to submit this vote? You can vote only once.");
            if (!confirmed) {
                event.preventDefault();
            }
        });
    }
});
