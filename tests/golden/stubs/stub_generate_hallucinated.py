"""Like stub_generate, but the evidence quote is not in the source, so the engine must reject the output."""
import stub_generate

stub_generate.HALLUCINATE = True
install = stub_generate.install
