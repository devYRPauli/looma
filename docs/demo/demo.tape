# Looma README demo - recorded with vhs (https://github.com/charmbracelet/vhs).
# Runs against a fully synthetic store (see gen_demo.py); no real data is shown.
#
#   pip install -e .
#   python docs/demo/gen_demo.py /tmp/looma-demo/demo.db
#   vhs docs/demo/demo.tape
#
# Assumes the demo store and its synthetic 'checkout' repo exist; adjust the paths
# in the hidden setup line below if you build the store elsewhere.

Output "demo.gif"

Set Shell "bash"
Set FontSize 15
Set Width 1200
Set Height 650
Set Padding 22
Set Theme "Catppuccin Mocha"
Set TypingSpeed 40ms
Set WindowBar Colorful

Hide
Type "export LOOMA_DB=/tmp/looma-demo/demo.db; cd /tmp/looma-demo/repos/checkout; clear"
Enter
Show

Sleep 800ms
Type "looma status"
Sleep 400ms
Enter
Sleep 3s

Type "clear"
Enter
Sleep 300ms

Type "looma brief"
Sleep 400ms
Enter
Sleep 4.5s

Type "clear"
Enter
Sleep 300ms

Type "looma pack"
Sleep 400ms
Enter
Sleep 4.5s

Type "clear"
Enter
Sleep 300ms

Type `looma ask "why are there duplicate charges"`
Sleep 400ms
Enter
Sleep 4s
