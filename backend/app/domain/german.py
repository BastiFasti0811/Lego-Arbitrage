"""Schreibweisen, in denen dieselbe deutsche Anzeige daherkommt.

Auf Kleinanzeigen wird der Umlaut oft umschrieben ("beschaedigt", "fuer") oder
ganz weggelassen ("beschadigt"). Jedes Muster, das auf ein Umlautwort hoert,
braucht alle drei Formen — sonst funktioniert es in einem Modul und im anderen
nicht, was genau passiert ist: der Zustands-Klassifizierer kannte die
Transkription, der Identity-Filter nur "[üu]", und ein "Stickerbogen passend
fuer Lego 10326" galt damit als das Set selbst.

Ein pauschaler Text-Fold waere die kuerzere Loesung und die falsche: "neue",
"teuer" und "Steuer" enthalten alle ein "ue", das kein Umlaut ist.
"""

AE = "(?:ä|ae|a)"
OE = "(?:ö|oe|o)"
UE = "(?:ü|ue|u)"
