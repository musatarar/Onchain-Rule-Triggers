from django.shortcuts import render
from django.views.decorators.csrf import ensure_csrf_cookie

# Every shell is public: it renders an empty #root and holds no data, and access
# control is the client-side guard in RequireAuth.tsx.


@ensure_csrf_cookie
def signin(request):
    """Render the sign-in page (password or magic link). Public: no data on it."""
    return render(request, "app/signin.html")


@ensure_csrf_cookie
def register(request):
    """Render the username/password registration page. Public: no data on it."""
    return render(request, "app/register.html")


@ensure_csrf_cookie
def auth_consume(request):
    """Render the token-consumption page. Public; the token is in the query string."""
    return render(request, "app/auth_consume.html")


@ensure_csrf_cookie
def journal(request):
    """Render the match journal shell, the page signing in lands on."""
    return render(request, "app/journal.html")


@ensure_csrf_cookie
def circuits(request):
    """Render the circuit list shell."""
    return render(request, "app/circuits.html")


@ensure_csrf_cookie
def circuit_new(request):
    """Render the circuit composer shell for a new circuit."""
    return render(request, "app/circuit_new.html")


@ensure_csrf_cookie
def circuit_edit(request, rule_id):
    """Render the circuit composer shell; the client loads rule ``rule_id`` itself."""
    return render(request, "app/circuit_edit.html")
