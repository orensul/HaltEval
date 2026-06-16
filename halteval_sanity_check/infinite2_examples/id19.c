typedef struct s_ent {
  unsigned int val;
  struct s_ent *next;
}	       ent_t;

void id19(ent_t *ent) {
  while (ent)
    ent = ent->next;
}