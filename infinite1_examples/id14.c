void id13(int*);

void id14(int y){
  while (y == 100)
    id13(&y);
  return;
}