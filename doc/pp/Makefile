koschei.pp.pdf: koschei.eps

%.eps: %.dia
	dia -e $@ $<

TEXINPUTS = .:$(RHBEAMER):
export TEXINPUTS

include $(RHBEAMER)/Makefile.common
