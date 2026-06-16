int id62();
int npc = 0;
int nx, ny, nz;
void id63(){
  int x = id62(), y = id62(), z = id62();
  if (y > 0) {
    do {
      if (npc == 5) {
        if (!((y < z && z <= nz) || (x < y && x >= nx) || 0))
          ;
      }
      if (npc == 0) {
        if (id62()) {
          nx = x;
          ny = y;
          nz = z;
          npc = 5;
        }
      }
      if (id62()) {
        x = x + y;
      } else {
        z = x - y;
      }
    } while (x < y && y < z);
  }
}
