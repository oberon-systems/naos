# The whole stack on one machine: compose for api, web and the database, the
# runner as this user. Everything the script creates lives in local/.

ROOT  := $(abspath ../..)
LOCAL := $(CURDIR)/local

.PHONY: kickstart runner down clean


# stack
kickstart runner down clean:
	ROOT=$(ROOT) LOCAL=$(LOCAL) MAKE=$(MAKE) bash kickstart.sh $@
