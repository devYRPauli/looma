# Demo

![Looma demo](demo.gif)

The GIF above is recorded against a **fully synthetic** store - two fake `acme`
repositories and made-up transcripts - so no real history is ever shown. It walks
through `status` -> `brief` -> `pack` -> `ask` on a sample `checkout` service.

## Reproduce it

```bash
pip install -e .                                      # exposes the `looma` binary
python docs/demo/gen_demo.py /tmp/looma-demo/demo.db  # build the synthetic store
vhs docs/demo/demo.tape                               # render demo.gif
```

- `gen_demo.py` creates the synthetic git repos + transcripts next to the database
  and ingests them.
- `demo.tape` is the [vhs](https://github.com/charmbracelet/vhs) script for the
  recording. It runs from inside the synthetic `checkout` repo so the commands need
  no `--project` flag.

Static stills are in [../screenshots/](../screenshots/).
